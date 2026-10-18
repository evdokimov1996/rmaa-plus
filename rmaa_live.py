"""Active-only live setup monitor: bounded ring, owned streams and latest result.

The preview uses known multitone band energies, not final session DSP. Independent
device latency/phase does not require synchronous callbacks. No timers, threads
or streams are created until start(); stop releases the same session lease used
by full measurements. Results never enter a full measurement report.
"""
from contextlib import ExitStack
from threading import Event,Lock,Thread
import time
import numpy as np
import sounddevice as sd
from rmaa_transport import session_lease,_com_apartment


class LiveAnalyzer:
    def __init__(self,fs):
        if not 32000<=fs<=384000:raise ValueError('Live monitor rate must be 32..384 kHz')
        self.fs=int(fs)
        points=[]
        for frequency in np.rint(np.geomspace(20,min(20000,.43*fs),52)).astype(int):
            if abs(frequency-1000)<6:continue
            if not points or frequency-points[-1]>=6:points.append(int(frequency))
        self.frequencies=np.array(sorted(set(points+[1000])))
        t=np.arange(fs,dtype=np.float64)/fs;wave=np.zeros(fs)
        for i,f in enumerate(self.frequencies):wave+=np.sin(2*np.pi*f*t+np.pi*i*(i-1)/len(self.frequencies))
        self.wave=(wave/max(abs(wave))).astype(np.float32)
        self.window=np.hanning(fs)
        bins=np.arange(-2,3)[None,:]+self.frequencies[:,None]
        self.bins=bins
        reference=abs(np.fft.rfft(self.wave*self.window))**2
        self.reference=np.sum(reference[bins],axis=1)
        mask=np.ones(len(reference),bool);mask[:20]=False;mask[min(20001,len(mask)):]=False
        for f in self.frequencies:mask[max(0,f-5):min(len(mask),f+6)]=False
        self.noise_bins=mask

    def response(self,audio):
        audio=np.asarray(audio,dtype=np.float32)
        if len(audio)!=self.fs or audio.ndim!=2 or not np.isfinite(audio).all():raise ValueError('One finite second of live audio required')
        spectrum=abs(np.fft.rfft(audio*self.window[:,None],axis=0))**2
        energies=np.sum(spectrum[self.bins],axis=1)
        floor=np.maximum(np.median(spectrum[self.noise_bins],axis=0)*5,1e-25)
        snr=energies/floor
        valid=snr>100
        gains=10*np.log10(np.maximum(energies,1e-30)/self.reference[:,None])
        anchor=np.flatnonzero(self.frequencies==1000)[0]
        gains-=gains[anchor]
        connected=np.mean(valid,axis=0)>.65
        valid&=connected[None,:]&valid[anchor][None,:]
        gains[~valid]=np.nan
        return {'frequency_hz':self.frequencies.copy(),'response_db':gains,'signal_found':connected.tolist()}


class LiveMonitor:
    def __init__(self,input_id,output_id,fs,mode,format_label,stream_options=None,level_dbfs=0):
        from rmaa_backend import PCM_FORMATS
        self.input_id=input_id;self.output_id=output_id;self.fs=int(fs);self.mode=mode
        self.dtype=PCM_FORMATS[format_label];self.format_label=format_label
        self.options=dict(stream_options or {});self.input_map=tuple(self.options.get('input_map',(1,2)))
        self.output_map=tuple(self.options.get('output_map',(1,2)))
        for mapping in (self.input_map,self.output_map):
            if len(mapping)!=2 or min(mapping)<1 or max(mapping)>64:raise ValueError('Channels must be 1..64')
        if len(set(self.output_map))!=2:raise ValueError('Two distinct output channels required')
        self.mono=len(set(self.input_map))==1;self.channels=1 if self.mono else 2
        self.analyzer=None
        self.stop_event=Event();self.done=Event();self.error=None;self.thread=None
        self._ring=np.zeros((2*self.fs,self.channels),np.float32)
        self._lock=Lock();self._result_lock=Lock();self._cursor=0;self._written=0
        self._latest=None;self._sequence=0;self._output_position=0;self._wire=None
        self._output_frames=0
        self._requested_level=0.;self.set_level(level_dbfs)

    def set_level(self,value):
        value=float(value)
        if not np.isfinite(value) or not -48<=value<=0:raise ValueError('Live level must be -48..0 dBFS')
        self._requested_level=value

    def start(self):
        if self.thread is not None:raise RuntimeError('Create a new monitor for each start')
        self.thread=Thread(target=self._run,daemon=True,name='RMAA-live-monitor');self.thread.start()

    def stop(self):self.stop_event.set()

    def latest(self):
        with self._result_lock:return self._latest

    def _make_wire(self,level):
        from rmaa_backend import pack_int24,_float_to_integer
        wave=self.analyzer.wave*10**(level/20)
        if self.dtype=='int24':return np.frombuffer(pack_int24(wave[:,None]),np.uint8).reshape(-1,3)
        if self.dtype=='uint8':return np.clip(np.rint(wave*128+128),0,255).astype(np.uint8)
        return _float_to_integer(wave,self.dtype)

    def _push(self,audio):
        total=len(audio);size=len(self._ring);audio=audio[-size:]
        with self._lock:
            start=(self._cursor+total-len(audio))%size
            first=min(len(audio),size-start)
            self._ring[start:start+first]=audio[:first]
            if first<len(audio):self._ring[:len(audio)-first]=audio[first:]
            self._cursor=(self._cursor+total)%size;self._written+=total

    def _snapshot(self,count):
        with self._lock:
            count=min(count,self._written,len(self._ring));end=self._cursor;start=(end-count)%len(self._ring)
            if not count:return None
            return self._ring[start:end].copy() if start<end else np.concatenate((self._ring[start:],self._ring[:end]))

    def _check_callback(self,status):
        if self.stop_event.is_set():raise sd.CallbackAbort
        fatal=bool(status)
        if status and hasattr(status,'priming_output'):
            fatal=any((status.input_underflow,status.input_overflow,status.output_underflow,status.output_overflow))
        if fatal:
            self.error='Audio discontinuity: '+str(status);self.stop_event.set();raise sd.CallbackAbort

    def _input(self,incoming,frames,status):
        indices=[self.input_map[0]-1] if self.mono else [c-1 for c in self.input_map]
        if self.dtype=='int24':
            raw=np.frombuffer(incoming,np.uint8).reshape(frames,self.inch,3)[:,indices,:].astype(np.int32)
            values=raw[:,:,0]|raw[:,:,1]<<8|raw[:,:,2]<<16
            values=((values^0x800000)-0x800000).astype(np.float32)/8388608
        elif self.dtype=='uint8':values=(np.frombuffer(incoming,np.uint8).reshape(frames,self.inch)[:,indices].astype(np.float32)-128)/128
        else:
            divisor={'int16':32768.,'int32':2147483648.,'float32':1.}[self.dtype]
            values=np.asarray(incoming[:,indices],dtype=np.float32)/divisor
        self._push(values)

    def _output(self,outgoing,frames):
        raw=self.dtype in ('int24','uint8')
        dest=np.frombuffer(outgoing,np.uint8).reshape((frames,self.outch,3) if self.dtype=='int24' else (frames,self.outch)) if raw else outgoing
        dest.fill(128 if self.dtype=='uint8' else 0)
        wire=self._wire;position=self._output_position;offset=0
        while offset<frames:
            count=min(frames-offset,len(wire)-position)
            for channel in self.output_map:dest[offset:offset+count,channel-1]=wire[position:position+count]
            offset+=count;position=(position+count)%len(wire)
        self._output_position=position
        self._output_frames+=frames

    def _callback(self,direction,*args):
        if direction=='both':incoming,outgoing,frames,_,status=args
        elif direction=='input':incoming,frames,_,status=args;outgoing=None
        else:outgoing,frames,_,status=args;incoming=None
        self._check_callback(status)
        try:
            if outgoing is not None:self._output(outgoing,frames)
            if incoming is not None:self._input(incoming,frames,status)
        except Exception as exc:
            self.error=str(exc);self.stop_event.set();raise sd.CallbackAbort

    def _run(self):
        try:
            with session_lease(),_com_apartment():self._run_owned()
        except Exception as exc:self.error=str(exc)
        finally:self.done.set()

    def _run_owned(self):
        try:
            from rmaa_backend import extras_for_mode
            settings=extras_for_mode(self.input_id,self.output_id,self.mode) or (None,None)
            native=sd.query_devices(self.input_id)['max_input_channels']
            self.inch=max(max(self.input_map),min(2,native));self.outch=max(self.output_map)
            sd.check_input_settings(device=self.input_id,channels=self.inch,samplerate=self.fs,dtype=self.dtype,extra_settings=settings[0])
            sd.check_output_settings(device=self.output_id,channels=self.outch,samplerate=self.fs,dtype=self.dtype,extra_settings=settings[1])
            self.analyzer=LiveAnalyzer(self.fs)
            current=self._requested_level;self._wire=self._make_wire(current)
            raw=self.dtype in ('int24','uint8')
            common={'samplerate':self.fs,'dtype':self.dtype,'dither_off':True,
                    'blocksize':self.options.get('blocksize',0),'latency':self.options.get('latency','high')}
            with ExitStack() as stack:
                if self.stop_event.is_set():return
                streams=[]
                if self.input_id==self.output_id:
                    factory=sd.RawStream if raw else sd.Stream
                    streams.append(stack.enter_context(factory(device=(self.input_id,self.output_id),channels=(self.inch,self.outch),extra_settings=settings,
                                                callback=lambda *args:self._callback('both',*args),**common)))
                else:
                    factory=sd.RawInputStream if raw else sd.InputStream
                    streams.append(stack.enter_context(factory(device=self.input_id,channels=self.inch,extra_settings=settings[0],callback=lambda *args:self._callback('input',*args),**common)))
                    factory=sd.RawOutputStream if raw else sd.OutputStream
                    streams.append(stack.enter_context(factory(device=self.output_id,channels=self.outch,extra_settings=settings[1],callback=lambda *args:self._callback('output',*args),**common)))
                stable=time.monotonic()+1.2;next_fft=stable;response=None;fft_id=0
                last_input=last_output=time.monotonic();input_count=self._written;output_count=self._output_frames
                while not self.stop_event.wait(.1):
                    now=time.monotonic()
                    if not all(stream.active for stream in streams):raise RuntimeError('Audio stream stopped or device disconnected')
                    if self._written!=input_count:last_input=now;input_count=self._written
                    if self._output_frames!=output_count:last_output=now;output_count=self._output_frames
                    if now-last_input>2 or now-last_output>2:raise TimeoutError('Live audio driver stopped delivering samples')
                    if current!=self._requested_level:
                        current=self._requested_level;self._wire=self._make_wire(current);stable=now+1.2
                    due=now>=next_fft and now>=stable
                    audio=self._snapshot(self.fs if due else self.fs//5)
                    if audio is None:continue
                    if not np.isfinite(audio).all():raise ValueError('Nonfinite live input')
                    meters=audio[-self.fs//5:]
                    peak=np.max(abs(meters),axis=0);rms=np.sqrt(np.mean(meters.astype(np.float64)**2,axis=0))
                    if due and len(audio)==self.fs:
                        response=self.analyzer.response(audio);next_fft=now+.4;fft_id+=1
                    self._sequence+=1
                    value={'sequence':self._sequence,'fft_id':fft_id,'peak_dbfs':20*np.log10(np.maximum(peak,1e-15)),
                           'rms_dbfs':20*np.log10(np.maximum(rms,1e-15)),'clipping':(peak>=.999).tolist(),
                           'response':response,'settling':now<stable,'output_level_dbfs':current}
                    with self._result_lock:self._latest=value
        except Exception as exc:self.error=str(exc)
