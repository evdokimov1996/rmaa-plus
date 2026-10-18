"""RMAA 6.4.5 F9 SAV records, independently implemented from file observations.

All nine native record types, including packed graphs, are retained. Native
pointer/unused fields are never dereferenced or copied to new files. Graphs
use unsigned little-endian 16-bit quantization; this is a file interchange
codec, not a copy of RightMark DSP or UI code.
"""
import base64
import math
import struct
from pathlib import Path
import numpy as np

MAX_BYTES=128*1024*1024
MAX_PLOT_POINTS=32768
DELTAS=(40,44,32,40,24,32,32,24,24)
RATES=(44100,48000,88200,96000,176400,192000,352800,384000)
FFT_RECORDS=(1,2,3,4)


def _display_series(encoded,index,fs,n,fft):
    values=encoded[index]
    if len(values)>MAX_PLOT_POINTS:
        step=math.ceil(len(values)/MAX_PLOT_POINTS)
        groups=len(values)//step
        indices=np.arange(groups)*step+values[:groups*step].reshape(groups,step).argmax(axis=1)
        if groups*step<len(values):indices=np.r_[indices,groups*step+int(np.argmax(values[groups*step:]))]
    else:indices=np.arange(len(values))
    if fft:
        x=indices*fs/n
        y=values[indices].astype(np.float64)*350/65000-300
    else:
        x=encoded[0][indices].astype(np.float64)
        y=values[indices].astype(np.float64)*300/65000-200
    return x.tolist(),y.tolist()


def load_sav(path):
    p=Path(path)
    with p.open('rb') as file:raw=file.read(MAX_BYTES+1)
    return _decode_sav(raw,p)


def _decode_sav(raw,p):
    if not 171<=len(raw)<=MAX_BYTES:raise ValueError('SAV outside the supported size budget')
    if raw[0]!=0xf9:raise ValueError('Unsupported SAV version: expected RMAA 6.4.5 F9')
    def text(offset):return raw[offset:offset+50].split(b'\0',1)[0].decode('cp1251',errors='replace')
    cursor=151;records=[];graphs=[];rate=None
    for record,delta in enumerate(DELTAS):
        if cursor+4>len(raw):raise ValueError('Truncated SAV record length')
        size=struct.unpack_from('<I',raw,cursor)[0]
        if size==0:
            records.append(None);graphs.append(None);cursor+=4;continue
        end=cursor+4+size
        if size<delta+20 or end>len(raw):raise ValueError('Invalid SAV record extent')
        fs,bits,channels,window,scale,n=struct.unpack_from('<6I',raw,cursor+delta)
        if fs not in RATES or bits not in (8,16,24,32) or channels not in (1,2) or window>64 or scale>64 or n>8388608:
            raise ValueError('Unsupported SAV graph header')
        if rate is None:rate=fs
        if fs!=rate:raise ValueError('Inconsistent SAV sample rates')
        stats=list(struct.unpack_from('<'+'f'*((delta-8)//4),raw,cursor+4))
        known=(8,8,6,6,4,6,6,4,4)[record]
        stats[known:]=[0.]*(len(stats)-known)
        if not all(math.isfinite(v) for v in stats):raise ValueError('Nonfinite SAV statistics')
        if record in (3,4,6) and any(not 0<=v<=1e6 for v in stats[:6 if record in (3,6) else 4]):raise ValueError('Invalid SAV percentage')
        payload=raw[cursor+delta+24:end]
        fft=record in FFT_RECORDS
        if n:
            expected=(n//2+1)*channels*2 if fft else n*(channels+1)*2
            if len(payload)!=expected or (fft and (n<2 or n&(n-1))):raise ValueError('SAV graph size/header mismatch')
        elif payload:raise ValueError('Unexpected empty-graph payload')
        graph={'kind':'fft' if fft else 'points','n':n,'fs':fs,'bits':bits,'channels':channels,'window':window,'scale':scale,'encoded_u16':base64.b64encode(payload).decode('ascii')}
        records.append(stats);graphs.append(graph);cursor=end
    if cursor+20!=len(raw) or struct.unpack_from('<I',raw,cursor)[0]!=16:raise ValueError('Unknown SAV footer/extension')
    _,g1,g2,latency=struct.unpack_from('<Ifff',raw,cursor+4)
    if not all(math.isfinite(v) and abs(v)<=1e6 for v in (g1,g2,latency)):raise ValueError('Invalid SAV calibration')
    result={'left':{},'right':{},'fs':rate or 48000,'source':str(p),'source_format':'RMAA F9 SAV','sav_statistics':records,
            'device_name':text(1),'sampling_mode':text(51),'comment':text(101),
            'algorithm':'Imported RMAA 6.4.5 F9 SAV statistics and packed graphs; file producer is not independently authenticated',
            'calibration':{'level_change_db':[g1,g2]},'sav_graphs':graphs,
            'sav_footer':{'flags':struct.unpack_from('<I',raw,cursor+4)[0],'latency':latency},
            'limitations':['Original statistics retain RMAA units and normalization',
                           'Large imported plots show peak-preserving envelopes; original packed bins remain available for lossless SAV graph export',
                           'SAV F9 does not store Open Lab phase/impulse, CCIF 19/20 kHz or THD-vs-level extensions']}
    for c,side in enumerate(('left','right')):
        data=result[side]
        fr=records[0]
        if fr:
            a=fr[c*4:c*4+4];data['response']={'min_20_20000_db':a[0],'max_20_20000_db':a[1],'min_db':a[2],'max_db':a[3]}
        no=records[1]
        if no:data['noise']={'noise_dbfs_rms':no[c],'noise_a_dbfs_rms':no[c+2],'peak_dbfs':no[c+4],'dc_offset':no[c+6]/100}
        dr=records[2]
        if dr:data['dynamic_range']={'dynamic_range_db':dr[c],'dynamic_range_a_db':dr[c+2]}
        th=records[3]
        if th:data['tone']={'thd_pct':th[c],'thdn_pct':th[c+2],'thdn_db':20*math.log10(max(th[c+2]/100,1e-18)),'thdn_a_db':20*math.log10(max(th[c+4]/100,1e-18))}
        im=records[4]
        if im:data['imd_smpte']={'imd_noise_pct':im[c],'imd_noise_a_pct':im[c+2]}
        im=records[6]
        if im:
            data['imd_sweep']=[{'frequency_hz':f,'imd_noise_pct':im[j*2+c]} for j,f in enumerate((5000,10000,15000))]
            data['imd_10k']={'imd_noise_pct':im[2+c]}
        for i,section in ((0,'response'),(1,'noise'),(2,'dynamic_range'),(3,'tone'),(4,'imd_smpte'),(6,'imd_frequency'),(7,'acoustic_response'),(8,'thd_frequency_curve')):
            graph=graphs[i]
            if not graph or not graph['n']:continue
            count=graph['n']//2+1 if graph['kind']=='fft' else graph['n']
            encoded=np.frombuffer(base64.b64decode(graph['encoded_u16']),'<u2').reshape(graph['channels'] if graph['kind']=='fft' else graph['channels']+1,count)
            channel=min(c,graph['channels']-1)+(0 if graph['kind']=='fft' else 1)
            x,y=_display_series(encoded,channel,graph['fs'],graph['n'],graph['kind']=='fft')
            if i==0:data['response'].update(frequency_hz=x,response_db=y)
            elif i in FFT_RECORDS:data[section]['spectrum']={'frequency_hz':x,'amplitude_dbfs_peak':y}
            else:data[section]={'frequency_hz':x,'value_db':y}
    xt=records[5]
    if xt:
        graph=graphs[5]
        for c,direction in enumerate(('L_to_R','R_to_L')):
            points=[{'frequency_hz':f,'crosstalk_db':xt[j*2+c]} for j,f in enumerate((100,1000,10000))]
            if graph and graph['n']:
                encoded=np.frombuffer(base64.b64decode(graph['encoded_u16']),'<u2').reshape(graph['channels']+1,graph['n'])
                x,y=_display_series(encoded,min(c,graph['channels']-1)+1,graph['fs'],graph['n'],False)
                points=[{'frequency_hz':f,'crosstalk_db':v} for f,v in zip(x,y)]
            result['crosstalk_'+direction]={'frequency_hz':1000,'crosstalk_db':xt[2+c],'curve':points}
    return result


def _quantize(values,span,offset):
    values=np.asarray(values,dtype=np.float64)
    if not np.isfinite(values).all():raise ValueError('Nonfinite SAV graph values')
    return np.clip(np.floor((values+offset)*65000/span+.5),0,65535).astype('<u2')


def _make_graph(result,record):
    fs=int(result['fs']);sides=[result.get('left',{}),result.get('right') or result.get('left',{})]
    if record==0 and result.get('type')=='rmaa-open-lab-acoustic-result':return None
    if record in FFT_RECORDS:
        section={1:'noise',2:'dynamic_range',3:'tone',4:'imd_smpte'}[record]
        specs=[s.get(section,{}).get('spectrum') for s in sides]
        if not all(specs):return None
        n=16384;frequencies=np.arange(n//2+1)*fs/n;payload=[]
        for spec in specs:
            y=np.interp(frequencies,spec['frequency_hz'],spec['amplitude_dbfs_peak'])
            payload.append(_quantize(y,350,300).tobytes())
        return {'kind':'fft','n':n,'fs':fs,'bits':32,'channels':2,'window':3,'scale':1,'encoded_u16':base64.b64encode(b''.join(payload)).decode('ascii')}
    x=None;ys=[]
    for c,s in enumerate(sides):
        if record==0:
            curve=s.get('response',{});xx=curve.get('frequency_hz');yy=curve.get('response_db')
        elif record==5:
            curve=result.get('crosstalk_L_to_R' if c==0 else 'crosstalk_R_to_L',{}).get('curve',[])
            xx=[r['frequency_hz'] for r in curve];yy=[r['crosstalk_db'] for r in curve]
        elif record==6:
            curve=s.get('imd_sweep',[]);xx=[r['frequency_hz'] for r in curve]
            yy=[20*math.log10(max(r.get('imd_noise_pct',r.get('imd_pct',0))/100,1e-18)) for r in curve]
        elif record==7:
            curve=s.get('acoustic_response',s.get('response',{}) if result.get('type')=='rmaa-open-lab-acoustic-result' else {})
            xx=curve.get('frequency_hz');yy=curve.get('response_db',curve.get('value_db'))
        else:
            curve=[r for r in s.get('thd_frequency',[]) if r.get('thd_pct') is not None];xx=[r['frequency_hz'] for r in curve]
            yy=[20*math.log10(max(r['thd_pct']/100,1e-18)) for r in curve]
        if xx is None or yy is None or len(xx)<2 or len(xx)!=len(yy):return None
        xx=np.asarray(xx,dtype=float);yy=np.asarray(yy,dtype=float)
        if not np.isfinite(xx).all() or np.any(xx<0) or np.any(xx>65535) or np.any(np.diff(xx)<=0):raise ValueError('SAV point frequencies must increase in 0..65535 Hz')
        if x is None:x=xx
        ys.append(np.interp(x,xx,yy))
    if len(x)>65535:
        indices=np.unique(np.linspace(0,len(x)-1,65535).astype(int));x=x[indices];ys=[y[indices] for y in ys]
    qx=np.floor(x+.5).astype('<u2')
    keep=np.r_[True,np.diff(qx.astype(int))>0];qx=qx[keep];ys=[y[keep] for y in ys]
    payload=qx.tobytes()+b''.join(_quantize(y,300,200).tobytes() for y in ys)
    return {'kind':'points','n':len(qx),'fs':fs,'bits':32,'channels':2,'window':3,'scale':1,'encoded_u16':base64.b64encode(payload).decode('ascii')}


def save_sav(path,result,label=None):
    """Write F9 with zeroed native pointer/padding fields and explicit absent tests.

    Extra Open Lab phase/19-20k CCIF/THD-level data stay in JSON. Native F9 has
    no slots for these extensions. Existing destination files are never replaced.
    """
    fs=int(result['fs'])
    if fs not in RATES:raise ValueError('Unsupported SAV sample rate')
    target=Path(path)
    if target.exists():raise FileExistsError('Choose a new SAV filename')
    sides=[result.get('left',{}),result.get('right') or result.get('left',{})]
    text=lambda x:str(x).encode('cp1251',errors='replace')[:49].ljust(50,b'\0')
    name=label or result.get('device_name','RMAA Open Lab')
    mode=result.get('sampling_mode',f'32-bit, {fs/1000:g} kHz'+(' MONO' if not result.get('right') else ''))
    data=bytearray(b'\xf9'+text(name)+text(mode)+text(result.get('comment','Independent Open Lab measurement')))
    packed=result.get('sav_graphs')
    count=0
    def field(section,key,c,default=0.):
        value=sides[c].get(section,{}).get(key,default)
        if value is None:value=default
        value=float(value)
        if not math.isfinite(value):raise ValueError('Nonfinite SAV statistic')
        return value
    for record,delta in enumerate(DELTAS):
        graph=packed[record] if isinstance(packed,list) and len(packed)==9 else _make_graph(result,record)
        if graph is None or not graph['n']:
            data+=bytes(4);continue
        if graph.get('fs')!=fs or graph.get('kind')!=('fft' if record in FFT_RECORDS else 'points'):
            raise ValueError('SAV graph sample rate/type differs from its result record')
        if len(graph['encoded_u16'])>((MAX_BYTES+2)//3)*4:
            raise ValueError('SAV packed graph exceeds the configured size budget')
        original_stats=result.get('sav_statistics')
        if packed and isinstance(original_stats,list) and len(original_stats)==9 and original_stats[record] is not None:
            values=original_stats[record]
        else:
            if record==0:values=[field('response',k,c) for c in (0,1) for k in ('min_20_20000_db','max_20_20000_db','min_db','max_db')]
            elif record==1:
                values=[]
                for key in ('noise_dbfs_rms','noise_a_dbfs_rms','peak_dbfs','dc_offset'):
                    for c in (0,1):
                        nk={'noise_dbfs_rms':'normalized_noise_dbfs_rms','noise_a_dbfs_rms':'normalized_noise_a_dbfs_rms'}.get(key,key)
                        values.append(field('noise',nk,c,field('noise',key,c))*(100 if key=='dc_offset' else 1))
                values.append(0.)
            elif record==2:values=[field('dynamic_range',key,c) for key in ('dynamic_range_db','dynamic_range_a_db') for c in (0,1)]+[0.,0.]
            elif record==3:values=[field('tone',key,c) for key in ('thd_pct','thdn_pct') for c in (0,1)]+[100*10**(field('tone','thdn_a_db',c,-360)/20) for c in (0,1)]+[0.,0.]
            elif record==4:
                if any('imd_noise_a_pct' not in s.get('imd_smpte',{}) for s in sides):
                    raise ValueError('This legacy result has no A-weighted IMD measurement; reanalyze the raw recording or use JSON')
                values=[field('imd_smpte',key,c) for key in ('imd_noise_pct','imd_noise_a_pct') for c in (0,1)]
            elif record==5:
                values=[]
                for f in (100,1000,10000):
                    for direction in ('L_to_R','R_to_L'):
                        curve=result['crosstalk_'+direction]['curve'];values.append(float(np.interp(f,[r['frequency_hz'] for r in curve],[r['crosstalk_db'] for r in curve])))
            elif record==6:
                values=[]
                for f in (5000,10000,15000):
                    for c in (0,1):
                        rows=sides[c].get('imd_sweep',[])
                        point=next((r for r in rows if abs(r['frequency_hz']-f)<1),None)
                        if point is None:raise ValueError('SAV IMD sweep requires measured 5/10/15 kHz points; use JSON for sparse data')
                        values.append(float(point.get('imd_noise_pct',point.get('imd_pct'))))
            else:
                values=[]
                for c in (0,1):
                    payload=np.frombuffer(base64.b64decode(graph['encoded_u16']),'<u2').reshape(3,graph['n'])
                    y=payload[c+1].astype(float)*300/65000-200;values.extend((float(min(y)),float(max(y))))
        if len(values)!=(delta-8)//4 or not all(math.isfinite(v) for v in values):raise ValueError('SAV scalar layout mismatch')
        raw_graph=base64.b64decode(graph['encoded_u16'],validate=True)
        expected=(graph['n']//2+1)*graph['channels']*2 if graph['kind']=='fft' else graph['n']*(graph['channels']+1)*2
        if len(raw_graph)!=expected:raise ValueError('SAV packed graph size mismatch')
        body=struct.pack('<'+'f'*len(values),*values)+bytes(4)+struct.pack('<6I',fs,graph['bits'],graph['channels'],graph['window'],graph['scale'],graph['n'])+raw_graph
        if len(data)+len(body)+24>MAX_BYTES:raise ValueError('SAV exceeds the configured size budget')
        data+=struct.pack('<I',len(body))+body;count+=1
    if not count:raise ValueError('No measured tests representable by native SAV F9; use JSON')
    calibration=result.get('calibration',{})
    gains=calibration.get('level_change_db',[calibration.get('calibration_tone_gain_db',0.)]*2)
    gains=[float(v or 0.) for v in gains]
    if len(gains)!=2 or not all(math.isfinite(v) for v in gains):raise ValueError('Invalid SAV gain')
    flags=int(result.get('sav_footer',{}).get('flags',0))
    if not 0<=flags<=65535:raise ValueError('Unsupported SAV footer flags')
    data+=struct.pack('<IIfff',16,flags,gains[0],gains[1],float(result.get('sav_footer',{}).get('latency',0.)))
    if len(data)>MAX_BYTES:raise ValueError('SAV exceeds the configured size budget')
    # Validate the entire constructed interchange file before creating a target.
    # This also checks packed graphs received through editable JSON sessions.
    _decode_sav(bytes(data),target)
    with target.open('xb') as file:file.write(data)
    return target
