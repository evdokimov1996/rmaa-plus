"""Measurement session: coherent test sequence, offline WAV import, and duplex recording.

Separates stimulus, recording, alignment and numerical analysis.
No original RMAA executable/resources are needed.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy import signal

from rmaa_dsp import (rms, db, tone, multitone, multitone_response,
                      noise_stats, tone_metrics, dynamic_range, imd_signal,
                      imd_metrics, crosstalk, stereo_balance, delay_impulse)

TEST_STAGES={
    "response":("frequency_response",),"noise":("noise",),
    "dynamic_range":("dynamic_range",),"thd":("thd",),"thdn":("thd",),
    "imd_smpte":("imd_smpte",),"imd_ccif":("imd_ccif",),"imd_10k":("imd_10k",),
    "imd_sweep":("imd_sweep",),"crosstalk":("crosstalk_L","crosstalk_R"),
    "phase":("phase_impulse",)}


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        from rmaa_transport import MeasurementCancelled
        raise MeasurementCancelled("Измерение остановлено")


def _ster(x):
    a=np.asarray(x,dtype=np.float32)
    return np.column_stack((a,a))

def _audio_stage(name, data, metadata=None):
    arr=np.asarray(data,dtype=np.float32)
    if arr.ndim!=2 or arr.shape[1]!=2:
        raise ValueError("Expected 2-channel audio")
    return {"name":name,"audio":arr, "metadata":metadata or {}}

def create_sequence(fs=48000, level_dbfs=0, lite=False, selected_tests=None, profile="quick"):
    """Return stereo stimuli at the requested sample peak (default 0 dBFS)."""
    if fs not in (44100,48000,88200,96000,176400,192000,352800,384000):
        raise ValueError("Unsupported session sample rate")
    if not -36 <= level_dbfs <= 0:
        raise ValueError("Test generator level must be -36 .. 0 dBFS")
    if profile not in ("quick", "precision"):
        raise ValueError("Unknown measurement profile")
    precision=profile=="precision" and not lite
    seconds=6. if precision else (.75 if lite else 1.5)
    selected = list(TEST_STAGES) if selected_tests is None else list(dict.fromkeys(selected_tests))
    if not selected or any(key not in TEST_STAGES for key in selected):
        raise ValueError("Select at least one valid measurement")
    required = {"calibration_tone"}|{stage for key in selected for stage in TEST_STAGES[key]}
    # Reject an oversized finite session before allocating any stimulus buffers.
    # The public transport budget is deliberately shared with offline manifests.
    estimate=4.+sum((1. if name=="calibration_tone" else
                    20. if name=="frequency_response" and precision else
                    max(1.,seconds) if name in ("frequency_response","phase_impulse") else
                    76.8 if name=="imd_sweep" and precision else
                    3*(max(.85,seconds)+.12) if name=="imd_sweep" else seconds)+.16
                    for name in required)
    if estimate*fs>19_900_000:
        raise ValueError("Выбранный точный тест превышает лимит памяти. Выберите меньше измерений или частоту 96 кГц / ниже.")
    quiet=lambda n: np.zeros((round(n*fs),2),dtype=np.float32)
    random=np.random.default_rng(4299)
    probe = random.standard_normal(round(.32*fs)).astype(np.float32)
    probe /= max(np.max(np.abs(probe)),1e-12)
    probe *= 10**(level_dbfs/20)
    ramp=min(512,len(probe)//4)
    probe[:ramp]*=np.linspace(0,1,ramp)
    probe[-ramp:]*=np.linspace(1,0,ramp)
    markers=[quiet(1.0),_ster(probe),quiet(.3)]
    response_meta={}
    if precision and "frequency_response" in required:
        from rmaa_dense_response import create_response
        mult,response_meta=create_response(fs,level_dbfs)
        freq=np.empty(0)
    else:
        mult,freq=multitone(fs,seconds=max(1.,seconds),n_tones=44,peak_dbfs=level_dbfs) if "frequency_response" in required else (np.empty(0),np.empty(0))
        response_meta={"tones_hz":freq.tolist()}
    sr=tone(fs,f=1000.0,seconds=seconds,peak_dbfs=level_dbfs) if required.intersection(("thd","crosstalk_L","crosstalk_R")) else np.empty(0)
    sr_dyn=tone(fs,f=1000.0,seconds=seconds,peak_dbfs=-60) if "dynamic_range" in required else np.empty(0)
    smpte=imd_signal(fs,"smpte",seconds=seconds,peak_dbfs=level_dbfs) if "imd_smpte" in required else np.empty(0)
    ccif=imd_signal(fs,"ccif",seconds=seconds,peak_dbfs=level_dbfs) if "imd_ccif" in required else np.empty(0)
    imd10=imd_signal(fs,"ccif_10k",seconds=seconds,peak_dbfs=level_dbfs) if "imd_10k" in required else np.empty(0)
    imd_sweep_meta=[]
    imd_sweep_parts=[]
    imd_loc=0
    imd_highs=(np.unique(np.rint(np.geomspace(550.,19000.,64))) if precision else
               (5000.,10000.,15000.)) if "imd_sweep" in required else ()
    for high in imd_highs:
        part=imd_signal(fs,"ccif_sweep",seconds=1. if precision else max(.85,seconds),
                        peak_dbfs=level_dbfs,high_hz=high)
        imd_sweep_parts.append(part)
        imd_sweep_meta.append({"high_hz":high,"mode":"ccif_sweep","start_frame":imd_loc,"frames":len(part)})
        pause=np.zeros(round((.2 if precision else .12)*fs),dtype=np.float32)
        imd_sweep_parts.append(pause)
        imd_loc+=len(part)+len(pause)
    imd_sweep_wave=np.concatenate(imd_sweep_parts) if imd_sweep_parts else np.empty(0)
    noise_probe=random.standard_normal(round(fs*max(1,seconds))).astype(np.float32)
    noise_probe/=max(float(np.max(np.abs(noise_probe))),1e-12)
    noise_probe*=10**(level_dbfs/20)
    xt_freq=np.unique(np.rint(np.r_[np.geomspace(20,20000,36),100,1000,10000]*len(sr)/fs))*fs/len(sr) if required.intersection(("crosstalk_L","crosstalk_R")) else np.empty(0)
    t=np.arange(len(sr))/fs
    xt_wave=sum((np.sin(2*np.pi*f*t+np.pi*j*(j-1)/len(xt_freq)) for j,f in enumerate(xt_freq)),np.zeros(len(sr)))
    xt_wave=(xt_wave/max(float(np.max(abs(xt_wave))) if len(xt_wave) else 0.,1e-20)*10**(level_dbfs/20)).astype(np.float32)
    stages=[
      _audio_stage("calibration_tone",_ster(tone(fs,f=1000.,seconds=1.,peak_dbfs=level_dbfs))),
      _audio_stage("frequency_response",_ster(mult),response_meta),
      _audio_stage("noise",quiet(seconds)),
      _audio_stage("dynamic_range",_ster(sr_dyn)),
      _audio_stage("thd",_ster(sr)),
      _audio_stage("imd_smpte",_ster(smpte)),
      _audio_stage("imd_ccif",_ster(ccif)),
      _audio_stage("imd_10k",_ster(imd10)),
      _audio_stage("imd_sweep",_ster(imd_sweep_wave),{"steps":imd_sweep_meta}),
      _audio_stage("crosstalk_L",np.column_stack((xt_wave,np.zeros_like(xt_wave))),{"tones_hz":xt_freq.tolist()}),
      _audio_stage("crosstalk_R",np.column_stack((np.zeros_like(xt_wave),xt_wave)),{"tones_hz":xt_freq.tolist()}),
      _audio_stage("phase_impulse",_ster(noise_probe))
    ]
    stages = [stage for stage in stages if stage["name"] in required]
    chunks=list(markers)
    loc=int(sum(len(c) for c in chunks))
    manifest=[]
    for stage in stages:
        chunks.append(stage["audio"])
        info={"name":stage["name"],"offset_frames":loc,"frames":len(stage["audio"]),"metadata":stage["metadata"]}
        manifest.append(info)
        loc+=len(stage["audio"])
        gap=quiet(.16)
        chunks.append(gap)
        loc+=len(gap)
    end_marker_offset=loc
    chunks.extend((_ster(_probe(fs,level_dbfs,len(probe),seed=4300)),quiet(.5)))
    result=np.concatenate(chunks,axis=0)
    if not np.isfinite(result).all() or np.max(abs(result))>1.:
        raise ValueError("Stimulus peak too high")
    return result,{"format_version":2,"fs":fs,"level_dbfs":level_dbfs,
                    "channels":2, "frames":int(len(result)),
                    "marker_offset_frames":int(len(markers[0])),
                    "marker_frames":len(probe),
                    "end_marker_offset_frames":end_marker_offset,
                    "end_marker_seed":4300,
                    "selected_tests":selected,"measurement_profile":"precision" if precision else "quick",
                    "stages":manifest,
                    "created":datetime.now(timezone.utc).isoformat(),
                    "note":"Independent test stimuli; not bitwise identical to RMAA."}

def _probe(fs,level_dbfs,frames,seed=4299):
    rng=np.random.default_rng(seed)
    vals=rng.standard_normal(frames).astype(np.float32)
    vals/=max(float(np.max(abs(vals))),1e-12)
    vals*=10**(level_dbfs/20)
    ramp=min(512,frames//4)
    vals[:ramp]*=np.linspace(0,1,ramp)
    vals[-ramp:]*=np.linspace(1,0,ramp)
    return vals

def find_recording_offset(captured,manifest,max_search_seconds=7):
    """Locate known random marker; return offset of capture vs original wave timeline."""
    x=np.asarray(captured,dtype=float)
    if x.ndim!=2 or x.shape[1]<2: raise ValueError("Recorded WAV must be stereo")
    fs=int(manifest["fs"])
    marker=_probe(fs,manifest["level_dbfs"],manifest["marker_frames"])
    span=min(len(x),int((max_search_seconds+1)*fs))
    if "end_marker_offset_frames" in manifest:
        span=min(span,int(manifest["end_marker_offset_frames"]))
    if span<len(marker): raise ValueError("Recorded WAV shorter than marker")
    # Correlate the shared audio band: a half-sample offset or driver low-pass
    # must not destroy a white marker's normalized similarity near Nyquist.
    sos=signal.butter(4,2000,fs=fs,output="sos")
    marker=signal.sosfilt(sos,marker)
    x=signal.sosfilt(sos,x[:span,:2],axis=0)
    # Search channels independently: averaging cancels an inverted-polarity channel.
    best=None
    for channel in (0,1):
        search=x[:span,channel]
        corr=signal.correlate(search,marker,mode="valid",method="fft")
        power=np.r_[0.,np.cumsum(search*search)]
        energy=np.maximum(power[len(marker):]-power[:-len(marker)],1e-30)
        normalized=np.where(energy>float(np.dot(marker,marker))*1e-8,abs(corr)/np.sqrt(energy*float(np.dot(marker,marker))),0.)
        peak=int(np.argmax(normalized))
        segment=search[peak:peak+len(marker)]
        similarity=float(abs(np.dot(segment,marker))/
                         (max(np.linalg.norm(segment),1e-30)*np.linalg.norm(marker)))
        candidate=(similarity,peak)
        if best is None or candidate[0]>best[0]:
            best=candidate
    similarity,peak=best
    if similarity<.6:
        raise ValueError(f"Calibration marker mismatch ({similarity:.2f}); wrong WAV or recording too noisy.")
    return peak-int(manifest["marker_offset_frames"]),similarity

def _cut(captured,info,lag):
    start=lag+info["offset_frames"]
    end=start+info["frames"]
    if start<0 or end>len(captured):
        raise ValueError(f"Incomplete recorded sequence: {info['name']} at {start}:{end}")
    x=np.asarray(captured[start:end,:2])
    if len(x)!=info["frames"]:raise ValueError("Stage length mismatch")
    return x

def _check_capture(x,manifest):
    if np.max(np.abs(x))>=.999:
        raise ValueError("Clipped recording detected; lower input gain")
    if not np.isfinite(x).all():
        raise ValueError("Non-finite audio samples")
    if x.ndim!=2 or x.shape[1]<2:raise ValueError("Stereo recording required")
    if np.max(np.abs(x))<1e-7:
        raise ValueError("Near-silent recording")

def align_clock(captured,manifest,cancel=None):
    """Estimate clock ratio from separate boundary markers and correct finite audio.

    Polyphase resampling is explicit in the report; it is not bit-perfect I/O.
    Legacy manifests without the end marker retain their previous timing.
    """
    if "end_marker_offset_frames" not in manifest:return captured,{"clock_check":"legacy manifest: no final marker"}
    fs=manifest["fs"]
    lag,_=find_recording_offset(captured,manifest)
    start=lag+manifest["marker_offset_frames"]
    length=manifest["marker_frames"]
    reference=_probe(fs,manifest["level_dbfs"],length)
    last=_probe(fs,manifest["level_dbfs"],length,seed=manifest.get("end_marker_seed",4299))
    def locate(marker,center,radius):
        lo=max(0,center-radius);hi=min(len(captured),center+radius+len(marker))
        sos=signal.butter(4,2000,fs=fs,output="sos")
        marker=signal.sosfilt(sos,marker)
        if hi-lo<len(marker):raise ValueError("Recording missing final clock marker")
        found=[]
        for channel in (0,1):
            search=signal.sosfilt(sos,captured[lo:hi,channel])
            corr=signal.correlate(search,marker,mode="valid",method="fft")
            power=np.r_[0.,np.cumsum(search*search)]
            energy=np.maximum(power[len(marker):]-power[:-len(marker)],1e-30)
            normalized=np.where(energy>float(np.dot(marker,marker))*1e-8,abs(corr)/np.sqrt(energy*float(np.dot(marker,marker))),0.)
            idx=int(np.argmax(normalized))
            segment=search[idx:idx+len(marker)]
            quality=abs(float(np.dot(segment,marker)))/max(float(np.linalg.norm(segment)*np.linalg.norm(marker)),1e-30)
            delta=0.
            if 0<idx<len(corr)-1:
                values=abs(corr[idx-1:idx+2]);div=values[0]-2*values[1]+values[2]
                if abs(div)>1e-30:delta=float(.5*(values[0]-values[2])/div)
            found.append((quality,lo+idx+delta))
        return max(found)
    quality,first=locate(reference,start,2)
    expected=lag+manifest["end_marker_offset_frames"]
    quality,last_position=locate(last,expected,round(.1*fs))
    if quality<.6:raise ValueError("Final marker mismatch: incomplete recording or excessive clock error")
    distance=manifest["end_marker_offset_frames"]-manifest["marker_offset_frames"]
    ratio=(last_position-first)/distance
    ppm=(ratio-1)*1e6
    if abs(ppm)>1000:raise ValueError(f"Clock error {ppm:+.1f} ppm exceeds the supported ±1000 ppm")
    marker_ppm=ppm
    pilot=next((p for p in manifest["stages"] if p["name"]=="calibration_tone"),None)
    pilot_gain=None
    if pilot:
        from rmaa_dsp import fit_frequency,sine_fit
        st=lag+pilot["offset_frames"]+round(.2*fs)
        end=lag+pilot["offset_frames"]+pilot["frames"]-round(.2*fs)
        wave=captured[st:end,0]
        measured=fit_frequency(wave,fs,1000.)
        ratio=1000./measured
        ppm=(ratio-1)*1e6
        amplitudes,_,_=sine_fit(wave,fs,[measured])
        pilot_gain=db(amplitudes[0])-manifest["level_dbfs"]
        if abs(ppm)>1000:raise ValueError("Calibration tone indicates excessive clock error")
    precise=manifest.get('measurement_profile')=='precision'
    applied=abs(ppm)>(.01 if precise else 1.)
    correction=1.
    if applied:
        if precise:
            from rmaa_dense_response import resample_clock_sinc
            correction=1./ratio
            captured=resample_clock_sinc(captured,ratio,cancel=cancel)
        else:
            captured,correction=_resample_legacy_clock(captured,ratio,cancel)
    return captured,{"clock_drift_ppm":ppm,"boundary_marker_drift_ppm":marker_ppm,
                     "clock_alignment_offset":int(round(last_position*correction-manifest["end_marker_offset_frames"])),
                     "calibration_tone_gain_db":pilot_gain,
                     "startup_alignment_warning":abs(marker_ppm-ppm)>20,"clock_correction_applied":applied,
                     "resampling_ratio":correction,"end_marker_similarity":quality,
                     "end_marker_capture_position":last_position,
                     "clock_residual_ppm":(ratio*correction-1)*1e6,
                     "clock_resampler":"128-tap Kaiser windowed sinc, arbitrary ratio" if precise else "rational polyphase"}


def _resample_legacy_clock(captured,ratio,cancel):
        from fractions import Fraction
        fraction=Fraction(1/ratio).limit_denominator(250000)
        correction=fraction.numerator/fraction.denominator
        check_cancel(cancel)
        # A metrology path needs a much narrower transition than SciPy's
        # default 20-tap-per-phase interpolator (which colours the 20 kHz band).
        rate=max(fraction.numerator,fraction.denominator)
        fir=signal.firwin(64*rate+1,1/rate,window=("kaiser",10.0))
        captured=signal.resample_poly(captured,fraction.numerator,fraction.denominator,axis=0,window=fir)
        check_cancel(cancel)
        return captured,correction


def validate_manifest(manifest):
    if not isinstance(manifest,dict) or manifest.get("format_version")!=2:
        raise ValueError("Unsupported measurement manifest")
    fs=manifest.get("fs")
    if type(fs)!=int or fs not in (44100,48000,88200,96000,176400,192000,352800,384000):raise ValueError("Invalid manifest rate")
    frames=manifest.get("frames")
    if type(frames)!=int or not fs//2<=frames<=20_000_000:raise ValueError("Invalid manifest duration")
    level=manifest.get("level_dbfs")
    if not isinstance(level,(int,float)) or not -36<=level<=0:raise ValueError("Invalid manifest level")
    for key in ("marker_offset_frames","marker_frames"):
        if type(manifest.get(key))!=int or not 1<=manifest[key]<=fs:raise ValueError("Invalid calibration marker")
    if manifest["marker_offset_frames"]+manifest["marker_frames"]>frames:raise ValueError("Marker outside recording")
    selected=manifest.get("selected_tests",list(TEST_STAGES))
    if not isinstance(selected,list) or not selected or any(k not in TEST_STAGES for k in selected):raise ValueError("Invalid selected tests")
    stages=manifest.get("stages")
    if not isinstance(stages,list) or not 1<=len(stages)<=16:raise ValueError("Invalid stages")
    allowed={"calibration_tone"}|{s for values in TEST_STAGES.values() for s in values}
    seen=set()
    end=manifest["marker_offset_frames"]+manifest["marker_frames"]
    for stage in stages:
        if not isinstance(stage,dict) or stage.get("name") not in allowed or stage["name"] in seen:raise ValueError("Invalid/duplicate stage")
        seen.add(stage["name"])
        start=stage.get("offset_frames");length=stage.get("frames")
        if type(start)!=int or type(length)!=int or start<end or length<fs//4 or start+length>frames:raise ValueError("Invalid/overlapping stage extent")
        end=start+length
        if stage["name"]=="imd_sweep":
            steps=stage.get("metadata",{}).get("steps")
            if not isinstance(steps,list) or not 1<=len(steps)<=64:raise ValueError("Invalid IMD steps")
            last=0
            for step in steps:
                off=step.get("start_frame");size=step.get("frames");freq=step.get("high_hz")
                if type(off)!=int or type(size)!=int or off<last or size<fs//4 or off+size>length or not isinstance(freq,(int,float)) or not 550<=freq<=19000:raise ValueError("Invalid IMD step extent")
                last=off+size
    required={s for key in selected for s in TEST_STAGES[key]}
    if not required.issubset(seen):raise ValueError("Manifest missing selected test stages")
    if "end_marker_offset_frames" in manifest:
        position=manifest["end_marker_offset_frames"]
        if type(position)!=int or position<end or position+manifest["marker_frames"]>frames:raise ValueError("Invalid final marker extent")


def analyze_recording(recorded,manifest,source="imported WAV",cancel=None,progress=None):
    """Works for live duplex or asynchronous recordings with known stimulus."""
    validate_manifest(manifest)
    fs=int(manifest["fs"])
    captured=np.asarray(recorded)
    if captured.ndim!=2 or captured.shape[1]<2:raise ValueError("Stereo recording required")
    captured=np.asarray(captured[:,:2])
    _check_capture(captured,manifest)
    original_capture=captured
    captured,clock_info=align_clock(captured,manifest,cancel)
    offset,score=find_recording_offset(captured,manifest)
    offset=clock_info.get("clock_alignment_offset",offset)
    dense_stage=next((stage for stage in manifest['stages'] if stage['name']=='frequency_response'
                      and stage.get('metadata',{}).get('method')=='periodic_dense_bla'),None)
    if dense_stage and manifest.get('measurement_profile')=='precision':
        from rmaa_dense_response import analyze_response,resample_clock_sinc
        check_cancel(cancel)
        diagnostic=analyze_response(_cut(captured,dense_stage,offset)[:,0],fs,
                                    dense_stage['metadata'],manifest['level_dbfs'],cancel=cancel)
        if diagnostic.get('quality')=='invalid_clock':
            residual=diagnostic.get('clock_residual_ppm')
            if residual is not None and abs(residual)<=20:
                # Correct the original audio once with the refined ratio, never
                # cascade interpolators or normalize amplitude from the answer.
                ratio=(1./clock_info.get('resampling_ratio',1.))/(1.+residual*1e-6)
                captured=resample_clock_sinc(original_capture,ratio,cancel=cancel)
                offset,score=find_recording_offset(captured,manifest)
                offset=int(round(clock_info['end_marker_capture_position']/ratio-manifest['end_marker_offset_frames']))
                clock_info.update(clock_alignment_offset=offset,resampling_ratio=1./ratio,
                                  dense_clock_refinement_ppm=residual,
                                  clock_correction_applied=True,
                                  clock_refinement_method='single global ratio refined from periodic response; final residual checked independently')
    del original_capture
    marker_start=offset+int(manifest["marker_offset_frames"])
    marker_length=int(manifest["marker_frames"])
    original_marker=_probe(fs,manifest["level_dbfs"],marker_length)
    got_marker=captured[marker_start:marker_start+marker_length,:2]
    ref_norm=float(np.dot(original_marker,original_marker))
    marker_gain=[float(np.dot(got_marker[:,c],original_marker)/ref_norm) for c in range(2)]
    calibration={
      "marker_gain_db":[db(abs(a)) for a in marker_gain],
      "phase_polarity":["normal" if a>=0 else "inverted" for a in marker_gain],
      "marker_similarity":score,
      "peak_dbfs":db(np.max(np.abs(captured))),
      "marker_offset_samples":offset,
      "input_gain_warning":any(db(abs(a)) < -30 for a in marker_gain),
      "clipping_warning":bool(np.max(np.abs(captured)) >= 0.98),
    }
    calibration.update(clock_info)
    check_cancel(cancel)
    stages={p["name"]:_cut(captured,p,offset) for p in manifest["stages"]}
    calibration['stage_peak_dbfs']={name:[db(np.max(abs(data[:,i]))) for i in range(2)]
                                    for name,data in stages.items()}
    selected=manifest.get("selected_tests",list(TEST_STAGES))
    legacy={"left":{},"right":{}}
    if "frequency_response" in stages:
        response_meta=next(p['metadata'] for p in manifest['stages'] if p['name']=='frequency_response')
        dense=response_meta.get('method')=='periodic_dense_bla'
        if not dense:
            reference,freq=multitone(fs,seconds=len(stages["frequency_response"])/fs,
                                 n_tones=44,peak_dbfs=manifest["level_dbfs"])
    if "phase_impulse" in stages:
        random=np.random.default_rng(4299)
        random.standard_normal(manifest["marker_frames"])
        phase_noise=random.standard_normal(len(stages["phase_impulse"])).astype(np.float32)
        phase_noise/=max(float(np.max(abs(phase_noise))),1e-12)
        phase_noise*=10**(manifest["level_dbfs"]/20)
    from spectrum_tools import compute_spectrum, SpectrumOptions
    spectral_stages={"noise":"noise","dynamic_range":"dynamic_range","thd":"tone",
                     "imd_smpte":"imd_smpte","imd_ccif":"imd_ccif","imd_10k":"imd_10k"}
    count=0
    mono=len(set(manifest.get("input_map",(1,2))))==1
    sides=('left',) if mono else ('left','right')
    total=max(1,len(stages)*len(sides))
    for i,side in enumerate(sides):
        result=legacy[side]
        for name,data in stages.items():
            check_cancel(cancel)
            wave=data[:,i]
            if name=='noise' and manifest.get('measurement_profile')=='precision':
                wave=wave[round(.25*fs):-round(.25*fs)]
            if name=="frequency_response":
                if dense:
                    from rmaa_dense_response import analyze_response
                    extent=next(stage for stage in manifest['stages'] if stage['name']==name)
                    start=offset+extent['offset_frames'];end=start+extent['frames']
                    result['response']=analyze_response(wave,fs,response_meta,manifest['level_dbfs'],cancel=cancel,
                        allow_period_clock_tracking=True,
                        context_before=captured[max(0,start-128):start,i],
                        context_after=captured[end:end+128,i])
                else:
                    result["response"]=multitone_response(reference,wave,fs,freq)
            elif name=="noise":result["noise"]=noise_stats(wave,fs)
            elif name=="thd":result["tone"]=tone_metrics(wave,fs,f=1000.)
            elif name=="dynamic_range":result["dynamic_range"]=dynamic_range(wave,fs,f=1000.)
            elif name=="imd_smpte":result[name]=imd_metrics(wave,fs,"smpte")
            elif name=="imd_ccif":result[name]=imd_metrics(wave,fs,"ccif")
            elif name=="imd_10k":result[name]=imd_metrics(wave,fs,"ccif_10k")
            elif name=="phase_impulse":
                result["phase"]=delay_impulse(phase_noise,wave,fs)
                result["phase"]["session_latency_ms"]=offset*1000/fs+result["phase"]["latency_ms"]
            elif name=="imd_sweep":
                meta=next(p for p in manifest["stages"] if p["name"]==name)
                result[name]=[]
                for st in meta['metadata']['steps']:
                    check_cancel(cancel)
                    result[name].append({"frequency_hz":st["high_hz"],**imd_metrics(
                        wave[st["start_frame"]:st["start_frame"]+st["frames"]],fs,st.get("mode","smpte"),high_hz=st["high_hz"])})
            if name in spectral_stages:
                nfft=min(131072 if manifest.get('measurement_profile')=='precision' else 16384,2**int(np.floor(np.log2(len(wave)))))
                spec=compute_spectrum(wave,fs,SpectrumOptions(fft_size=nfft))
                result[spectral_stages[name]]["spectrum"]={
                    "frequency_hz":spec["frequency_hz"].tolist(),
                    "amplitude_dbfs_peak":spec["amplitude_dbfs_peak"].tolist(),
                    "fft_size":spec['fft_size'],"window_count":spec['window_count'],
                    "resolution_hz":spec['resolution_hz'],"window":"kaiser","kaiser_beta":9.,
                    "amplitude_convention":"coherent sine peak per bin; noncoherent tones have window scalloping; scalar metrics use sinusoidal fits"}
            count+=1
            if progress:progress(count/total)
        if "response" in result and "phase" in result:
            result["response"]["latency_ms"]=result["phase"]["session_latency_ms"]
    extra={}
    gain=calibration.get("calibration_tone_gain_db")
    if gain is not None:
        for result in legacy.values():
            if "noise" in result:
                result["noise"]["normalized_noise_dbfs_rms"]=result["noise"]["noise_dbfs_rms"]-gain
                result["noise"]["normalized_noise_a_dbfs_rms"]=result["noise"]["noise_a_dbfs_rms"]-gain
    if "crosstalk_L" in stages and "crosstalk_R" in stages:
        for name,main,leak in (("L_to_R",0,1),("R_to_L",1,0)):
            wave=stages["crosstalk_L" if main==0 else "crosstalk_R"]
            row=crosstalk(wave[:,main],wave[:,leak],fs,f=1000.)
            metadata=next(p["metadata"] for p in manifest["stages"] if p["name"]==("crosstalk_L" if main==0 else "crosstalk_R"))
            frequencies=metadata.get("tones_hz")
            if frequencies:
                bins=np.rint(np.asarray(frequencies)*len(wave)/fs).astype(int)
                a=abs(np.fft.rfft(wave[:,main]))[bins]
                b=abs(np.fft.rfft(wave[:,leak]))[bins]
                row["curve"]=[{"frequency_hz":f,"crosstalk_db":db(v/max(u,1e-18))} for f,u,v in zip(frequencies,a,b)]
            extra["crosstalk_"+name]=row
    if "thd" in stages:
        extra["stereo_balance"]=stereo_balance(stages["thd"][:,0],stages["thd"][:,1],fs,f=1000.)
    mono=len(set(manifest.get("input_map",(1,2))))==1
    if mono:
        legacy["right"]={}
        extra.pop("stereo_balance",None)
        extra.pop("crosstalk_L_to_R",None)
        extra.pop("crosstalk_R_to_L",None)
    response_warnings=[side+': '+result['response']['reason'] for side,result in legacy.items()
                       if result.get('response',{}).get('valid') is False]
    return {
      "channel_mode":"mono" if mono else "stereo",
      **legacy,**extra,"fs":fs,"level_dbfs":manifest["level_dbfs"],
      "source":source,"date_utc":datetime.now(timezone.utc).isoformat(),
      "marker_alignment_samples":offset,"marker_coherence":score,"calibration":calibration,
      "selected_tests":selected,"algorithm":"independent RMAA Open Lab measurement suite "+('4 (precision)' if manifest.get('measurement_profile')=='precision' else '3'),
      "measurement_profile":manifest.get('measurement_profile','quick'),
      "limitations":["Noninferiority to original RMAA 6.4.5 is not established", "No absolute reference calibration",
                     "Clock correction is estimated from markers and calibration pilot; dense response independently checks residual drift"]+response_warnings}


def save_offline(directory,fs=48000,level_dbfs=0,selected_tests=None,profile='quick'):
    root=Path(directory)
    root.mkdir(parents=True,exist_ok=True)
    output,manifest=create_sequence(fs,level_dbfs,selected_tests=selected_tests,profile=profile)
    sf.write(str(root/"rmaa_test_sequence.wav"),output,fs,subtype="PCM_24")
    (root/"rmaa_test_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return root/"rmaa_test_sequence.wav",root/"rmaa_test_manifest.json"

def import_offline(wav,manifest_path,**options):
    if Path(manifest_path).stat().st_size>1_000_000:raise ValueError("Manifest too large")
    manifest=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    validate_manifest(manifest)
    with sf.SoundFile(str(wav)) as snd:
        if snd.samplerate!=int(manifest["fs"]):
            raise ValueError("Recorded WAV sample rate mismatches test manifest")
        if snd.channels==1:manifest={**manifest,'input_map':[1,1]}
        if snd.frames>20_000_000:
            raise ValueError("File exceeds configured size; trim recording")
        audio=np.empty((snd.frames,2),dtype=np.float32)
        for start in range(0,snd.frames,65536):
            block=snd.read(frames=min(65536,snd.frames-start),dtype="float32",always_2d=True)
            audio[start:start+len(block)]=np.repeat(block,2,axis=1) if snd.channels==1 else block[:,:2]
    return analyze_recording(audio,manifest,source=str(wav),**options)

def duplex_test(input_id,output_id,fs=48000,level_dbfs=0,
                api_mode="Автоматически",format_label="32-bit float",
                recording_path=None,selected_tests=None,cancel=None,progress=None,profile='quick',**stream_options):
    check_cancel(cancel)
    from rmaa_capture import preflight_capture,save_capture
    if recording_path:
        preflight_capture(recording_path)
    if len(set(stream_options.get("input_map",(1,2))))==1:
        selected_tests=[k for k in (list(TEST_STAGES) if selected_tests is None else selected_tests) if k!="crosstalk"]
    sequence,manifest=create_sequence(fs,level_dbfs,selected_tests=selected_tests,profile=profile)
    manifest.update(input_map=list(stream_options.get("input_map",(1,2))),
                    output_map=list(stream_options.get("output_map",(1,2))),
                    api_mode=api_mode,pcm_format=format_label)
    sequence=np.vstack((sequence,np.zeros((round(.7*fs),2),dtype=np.float32)))
    from rmaa_backend import playrec_with_format,api_for_device
    actual_host_api=api_for_device(input_id)
    captured=playrec_with_format(sequence,fs,input_id,output_id,
          mode=api_mode,format_label=format_label,cancel=cancel,
          progress=(lambda n:progress(.7*n)) if progress else None,**stream_options)
    # Preserve a completed raw capture even when analysis fails (clipping, clock drift,
    # missing marker). This is essential for diagnosing the actual measurement.
    saved={}
    if recording_path:
        saved=save_capture(recording_path,captured,fs,manifest)
    result=analyze_recording(captured,manifest,source="live duplex",cancel=cancel,
                            progress=(lambda n:progress(.7+.3*n)) if progress else None)
    result.update(saved,input_device=input_id,output_device=output_id,api_mode=api_mode,
                  actual_host_api=actual_host_api,portaudio_dither=False,
                  pcm_format=format_label)
    return result
