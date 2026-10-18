"""Owned, finite PortAudio streams. Cancellation closes only this session's stream.

Callback storage is preallocated; no disk I/O, UI calls or growing queues occur
on the audio thread. Overflow/underflow invalidates the measurement.
"""
from contextlib import contextmanager
import os

@contextmanager
def _com_apartment():
    initialized=False
    if os.name=="nt":
        import ctypes
        result=ctypes.windll.ole32.CoInitializeEx(None,0)
        initialized=result in (0,1)
    try:yield
    finally:
        if initialized:ctypes.windll.ole32.CoUninitialize()

from threading import Event
import numpy as np
import sounddevice as sd


class MeasurementCancelled(Exception):
    pass


def _transfer(audio, fs, input_id=None, output_id=None, *, dtype="float32",
             extra_settings=None, input_map=(1, 2), output_map=(1, 2),
             cancel=None, progress=None, blocksize=0, latency="high", ready=None):
    from rmaa_backend import pack_int24, unpack_int24, _float_to_integer
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim != 2 or x.shape[1] != 2 or not len(x) or not np.isfinite(x).all():
        raise ValueError("Finite nonempty stereo stimulus required")
    if input_id is None and output_id is None:
        raise ValueError("An input or output device is required")
    cancel = cancel if cancel is not None else Event()
    if cancel.is_set():
        raise MeasurementCancelled("Измерение остановлено")
    for mapping in (input_map, output_map):
        if len(mapping) != 2 or min(mapping) < 1 or max(mapping) > 64:
            raise ValueError("Choose two channel numbers in 1..64 (may coincide for mono input)")
    if output_id is not None and len(set(output_map)) != 2:
        raise ValueError("Output channels must be distinct; use a stereo output pair")
    inch, outch = max(input_map), max(output_map)
    if input_id is not None:
        native=sd.query_devices(input_id)["max_input_channels"]
        inch=max(inch,min(2,native))
    n = len(x)
    if not 8000<=fs<=384000 or n>20_000_000:
        raise ValueError("Session exceeds the supported rate/duration")
    if n*2*8>512*1024*1024:
        raise ValueError("Channel routing exceeds the 512 MiB stream allocation budget")
    if input_id is not None and output_id is not None and input_id!=output_id:
        # Different hardware clocks must never share a duplex callback cursor.
        # A finite input stream and finite output stream own independent cursors;
        # alignment and drift are measured from the captured stimulus afterwards.
        from threading import Thread
        shutdown=Event();input_ready=Event();input_done=Event();result={}
        # Input starts first. Keep recording beyond the last output frame instead
        # of relying on callers to have supplied sufficient trailing silence.
        input_latency=sd.query_devices(input_id).get("default_high_input_latency",0.)
        output_latency=sd.query_devices(output_id).get("default_high_output_latency",0.)
        margin=max(2.,float(input_latency)+float(output_latency)+1.)
        capture_template=np.zeros((n+round(margin*fs),2),np.float32)
        if len(capture_template)>20_000_000:raise ValueError("Capture plus driver-latency margin exceeds frame budget")
        class SessionCancel:
            def is_set(self):return cancel.is_set() or shutdown.is_set()
        token=SessionCancel()
        settings=extra_settings or (None,None)
        def record():
            try:
                result["audio"]=_transfer(capture_template,fs,input_id,None,dtype=dtype,
                    extra_settings=settings[0],input_map=input_map,output_map=output_map,
                    cancel=token,blocksize=blocksize,latency=latency,ready=input_ready)
            except Exception as exc:
                result["error"]=exc
                shutdown.set()
            finally:
                input_ready.set();input_done.set()
        worker=Thread(target=record,daemon=True)
        worker.start()
        try:
            if not input_ready.wait(5):raise TimeoutError("Input stream startup timeout")
            if "error" in result:raise result["error"]
            _transfer(x,fs,None,output_id,dtype=dtype,extra_settings=settings[1],
                input_map=input_map,output_map=output_map,cancel=token,progress=progress,
                blocksize=blocksize,latency=latency)
            if not input_done.wait(margin+5):raise TimeoutError("Input stream did not finish")
            if "error" in result:raise result["error"]
            return result["audio"]
        except MeasurementCancelled:
            if "error" in result:raise result["error"]
            raise
        finally:
            shutdown.set()
            worker.join(timeout=2)
            if worker.is_alive():
                _stuck_workers.append(worker)
                raise RuntimeError("Audio driver did not release its input stream; further sessions are blocked until it returns or the app restarts")
    received = np.empty((n, 2), dtype=np.float32) if input_id is not None else None
    done = Event()
    state = {"position": 0, "error": None}
    # Keep only two quantized stimulus channels, regardless of physical channel
    # numbers. Scatter into the driver's callback buffer; no n*64 routing buffer.
    wire_out=None
    raw = dtype in ("int24", "uint8")
    if output_id is not None:
        if dtype == "int24":
            wire_out=np.frombuffer(pack_int24(x),np.uint8).reshape(n,2,3)
        elif dtype == "uint8":
            wire_out=np.clip(np.rint(x*128+128),0,255).astype(np.uint8)
        else:
            wire_out=_float_to_integer(x,dtype)
    input_indices=tuple(int(c)-1 for c in input_map)
    output_indices=tuple(int(c)-1 for c in output_map)

    def callback(*args):
        if input_id is not None and output_id is not None:
            incoming, outgoing, frames, _, status = args
        elif input_id is not None:
            incoming, frames, _, status = args
            outgoing = None
        else:
            outgoing, frames, _, status = args
            incoming = None
        if outgoing is not None:
            if raw:
                raw_output=np.frombuffer(outgoing,np.uint8)
                raw_output.fill(128 if dtype=="uint8" else 0)
                raw_output=raw_output.reshape(frames,outch,3) if dtype=="int24" else raw_output.reshape(frames,outch)
            else:
                outgoing.fill(0)
        if cancel.is_set():
            raise sd.CallbackAbort
        fatal=bool(status)
        if status and hasattr(status,"priming_output"):
            fatal=any((status.input_underflow,status.input_overflow,status.output_underflow,status.output_overflow))
        if fatal:
            state["error"] = "Audio discontinuity: " + str(status)
            raise sd.CallbackAbort
        i = state["position"]
        count = min(frames, n-i)
        try:
            if outgoing is not None:
                dest=raw_output if raw else outgoing
                for c,physical in enumerate(output_indices):
                    dest[:count,physical]=wire_out[i:i+count,c]
            if incoming is not None:
                if dtype == "int24":
                    values = unpack_int24(bytes(incoming), inch)
                elif dtype == "uint8":
                    values = (np.frombuffer(incoming, np.uint8).reshape(-1, inch).astype(np.float32)-128)/128
                elif dtype == "int16":
                    values = np.asarray(incoming, dtype=np.float32)/32768
                elif dtype == "int32":
                    values = np.asarray(incoming, dtype=np.float64)/2147483648
                else:
                    values = incoming
                for c,physical in enumerate(input_indices):
                    received[i:i+count,c]=values[:count,physical]
            state["position"] += count
            if state["position"] >= n:
                raise sd.CallbackStop
        except (sd.CallbackStop, sd.CallbackAbort):
            raise
        except Exception as exc:
            state["error"] = str(exc)
            raise sd.CallbackAbort

    args = dict(samplerate=fs, dtype=dtype, callback=callback,
                finished_callback=done.set, blocksize=blocksize, latency=latency)
    if extra_settings is not None:
        args["extra_settings"] = extra_settings
    if input_id is not None and output_id is not None:
        factory = sd.RawStream if raw else sd.Stream
        args.update(device=(input_id, output_id), channels=(inch, outch))
    elif input_id is not None:
        factory = sd.RawInputStream if raw else sd.InputStream
        args.update(device=input_id, channels=inch)
    else:
        factory = sd.RawOutputStream if raw else sd.OutputStream
        args.update(device=output_id, channels=outch)
    # This bounded wait exists only during the active operation. It reports
    # progress and makes cancellation responsive even if the driver stops callbacks.
    import time
    deadline = time.monotonic() + n/fs + 15
    with _com_apartment(), factory(**args) as stream:
        if ready is not None:ready.set()
        while not done.wait(.1):
            if cancel.is_set():
                stream.abort()
                raise MeasurementCancelled("Измерение остановлено")
            if time.monotonic() > deadline:
                stream.abort()
                raise TimeoutError("Audio driver did not finish before the session deadline")
            if progress:
                progress(state["position"]/n)
    if cancel.is_set():
        raise MeasurementCancelled("Измерение остановлено")
    if state["error"]:
        raise RuntimeError(state["error"])
    if state["position"] != n:
        raise RuntimeError("Audio stream ended before the complete recording")
    if progress:
        progress(1.0)
    return received


# No session can overlap another owned session or a driver thread that has not
# released its device. State changes only on user actions, never an idle timer.
from threading import Lock
_session_lock=Lock()
_stuck_workers=[]


def ensure_session_available():
    if _session_lock.locked() or any(t.is_alive() for t in _stuck_workers):
        raise RuntimeError('An audio session still owns a stream; stop it before starting another')

@contextmanager
def session_lease():
    _stuck_workers[:]=[t for t in _stuck_workers if t.is_alive()]
    if _stuck_workers:raise RuntimeError("Previous audio driver operation still owns a stream; restart the app if the driver does not return")
    if not _session_lock.acquire(blocking=False):raise RuntimeError("An audio session is already active")
    try:yield
    finally:_session_lock.release()


def transfer(*args,**kwargs):
    with session_lease():return _transfer(*args,**kwargs)
