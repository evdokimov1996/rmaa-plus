"""Acoustic suite: standalone WAV export/import and live duplex execution.

No microphone or speaker-level output should be connected to the wrong input.
All results are relative (dBFS and dB referenced to 1 kHz), not absolute SPL.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf

from rmaa_acoustic import (
    AcousticConfig, exponential_sweep, mic_calibration_from_csv,
    stepped_tone_sequence, sweep_transfer, stepped_tone_analyze,
    thd_level_curve
)
from rmaa_dsp import tone
from rmaa_session import _probe, find_recording_offset

FORMAT_VERSION = 1
LEVELS_DBFS = (-42.0, -36.0, -30.0, -24.0, -18.0, -12.0, -6.0, 0.0)
STEP_FREQUENCIES = (40., 80., 160., 315., 630., 1000., 2000., 4000., 8000., 12000.)


def create_acoustic_sequence(cfg: AcousticConfig, step_seconds=None, lite=False):
    cfg.validate()
    if step_seconds is None:step_seconds={"standard":.75,"detailed":1.,"thorough":1.5,"subwoofer":1.5}[cfg.detail]
    if not .4<=step_seconds<=5:raise ValueError('Acoustic step duration must be 0.4..5 seconds')
    count=24 if cfg.detail=='thorough' else 16 if cfg.detail!='standard' else 10
    if cfg.sample_rate*(cfg.sweep_seconds+cfg.tail_seconds+count*(step_seconds+.32)+len(LEVELS_DBFS)*(step_seconds+.35)+3)>25_000_000:
        raise ValueError('Acoustic sequence exceeds frame budget; reduce duration or sample rate')
    fs=cfg.sample_rate
    gap=lambda duration: np.zeros((round(duration*fs), 2), dtype=np.float32)
    stereo=lambda mono: np.column_stack((mono,mono)).astype(np.float32)
    marker=_probe(fs,cfg.level_dbfs,round(.32*fs))
    samples=[gap(.25),stereo(marker),gap(.3)]
    position=sum(len(x) for x in samples)
    stages=[]

    def append(name, mono, extra):
        nonlocal position
        stage=stereo(mono)
        stages.append({"name":name,"offset_frames":position,"frames":len(stage),**extra})
        samples.append(stage)
        position += len(stage)
        pause=gap(.2)
        samples.append(pause)
        position += len(pause)

    sw=exponential_sweep(cfg)
    append("sweep",sw,{"start_hz":cfg.start_hz,"end_hz":cfg.end_hz,
                       "sweep_seconds":cfg.sweep_seconds})
    frequencies=STEP_FREQUENCIES if not lite else (100.,1000.,8000.)
    if not lite and cfg.detail!="standard":
        frequencies=np.geomspace(cfg.start_hz,cfg.end_hz,24 if cfg.detail=="thorough" else 16)
    frequencies=tuple(float(f) for f in frequencies if cfg.start_hz<=f<=min(cfg.end_hz,.43*fs))
    if len(frequencies)<2:frequencies=(cfg.start_hz,min(cfg.end_hz,.43*fs))
    steps,meta=stepped_tone_sequence(fs,frequencies=frequencies,
                   level_dbfs=cfg.level_dbfs,stage_seconds=step_seconds)
    append("thd_vs_frequency",steps,{"step_manifest":meta})
    levels=LEVELS_DBFS if not lite else (-36.0,-24.0,-12.0,0.0)
    levels=tuple(a for a in levels if a<=cfg.level_dbfs) or (cfg.level_dbfs,)
    offset=0
    level_meta=[]
    joined=[]
    for level in levels:
        carrier=min(997.,max(cfg.start_hz,cfg.end_hz/3))
        st=tone(fs, f=carrier, seconds=step_seconds, peak_dbfs=level)
        joined.append(st)
        level_meta.append({"level_dbfs":level, "start_frame":offset,"frames":len(st)})
        silence=np.zeros(round(.15*fs),dtype=np.float32)
        joined.append(silence)
        offset += len(st)+len(silence)
    append("thd_vs_level",np.concatenate(joined),{"level_manifest":level_meta,"frequency_hz":carrier})
    samples.append(gap(.5))
    wave=np.concatenate(samples,axis=0)
    if not np.isfinite(wave).all() or np.max(abs(wave)) > 1.:
        raise ValueError("Waveform exceeds digital full scale")
    manifest={
        "format_version":FORMAT_VERSION, "type":"rmaa-open-lab-acoustic",
        "fs":fs, "level_dbfs":cfg.level_dbfs,"detail":cfg.detail,
        "marker_offset_frames":len(samples[0]),"marker_frames":len(marker),
        "frames":len(wave),
        "stages":stages,
        "created":datetime.now(timezone.utc).isoformat(),
        "caution":"Not equivalent to RMAA reference signals",
    }
    return wave,manifest


def validate_acoustic_manifest(manifest):
    if manifest.get("format_version")!=1 or manifest.get("type")!="rmaa-open-lab-acoustic":
        raise ValueError("Not an RMAA Open Lab acoustic measurement manifest")
    def bounded(value,low,high):
        if type(value) is not int or not low<=value<=high:raise ValueError('Invalid acoustic manifest frame/rate extent')
    fs=manifest.get('fs');bounded(fs,32000,384000)
    frames=manifest.get('frames');bounded(frames,fs,25_000_000)
    bounded(manifest.get('marker_frames'),fs//10,fs)
    bounded(manifest.get('marker_offset_frames'),0,frames-manifest['marker_frames'])
    level=manifest.get('level_dbfs')
    if not isinstance(level,(float,int)) or not np.isfinite(level) or not -50<=level<=0:raise ValueError('Invalid acoustic level')
    stages=manifest.get('stages')
    if not isinstance(stages,list) or len(stages)!=3 or {s.get('name') for s in stages}!={'sweep','thd_vs_frequency','thd_vs_level'}:
        raise ValueError('Invalid acoustic stages')
    previous=manifest['marker_offset_frames']+manifest['marker_frames']
    for stage in stages:
        bounded(stage.get('offset_frames'),previous,frames)
        bounded(stage.get('frames'),1,frames-stage['offset_frames'])
        previous=stage['offset_frames']+stage['frames']
        if stage['name']=='sweep':
            seconds=stage.get('sweep_seconds')
            AcousticConfig(sample_rate=fs,start_hz=stage.get('start_hz'),end_hz=stage.get('end_hz'),sweep_seconds=seconds,
                level_dbfs=level,tail_seconds=(stage['frames']-round(seconds*fs))/fs).validate()
            continue
        steps=stage.get('step_manifest' if stage['name']=='thd_vs_frequency' else 'level_manifest')
        if not isinstance(steps,list) or not 1<=len(steps)<=256:raise ValueError('Invalid acoustic step count')
        end=0
        for step in steps:
            bounded(step.get('start_frame'),end,stage['frames'])
            bounded(step.get('frames'),fs//3,min(fs*10,stage['frames']-step['start_frame']))
            end=step['start_frame']+step['frames']
            value=step.get('frequency_hz' if stage['name']=='thd_vs_frequency' else 'level_dbfs')
            if not isinstance(value,(float,int)) or not np.isfinite(value):raise ValueError('Invalid acoustic step value')
            if stage['name']=='thd_vs_frequency' and not 0<value<.45*fs:raise ValueError('Invalid acoustic step frequency')
            if stage['name']=='thd_vs_level' and not -90<=value<=0:raise ValueError('Invalid acoustic step level')


def analyze_acoustic_capture(recorded,manifest,mic_calibration=None,source="offline"):
    validate_acoustic_manifest(manifest)
    fs=int(manifest["fs"])
    audio=np.asarray(recorded,dtype=np.float64)
    if audio.ndim!=2 or audio.shape[1]<2 or not np.isfinite(audio).all():
        raise ValueError("Finite stereo captured WAV required")
    peak=float(np.max(np.abs(audio)))
    if peak>.999:
        raise ValueError("Clipped recording; reduce the input gain")
    if peak<1e-7:
        raise ValueError("Near silent captured signal")
    lag,score=find_recording_offset(audio,manifest)
    stage_map={}
    for stage in manifest["stages"]:
        start=lag+int(stage["offset_frames"])
        stop=start+int(stage["frames"])
        if start<0 or stop>len(audio):
            raise ValueError("Incomplete captured stage: "+stage["name"])
        stage_map[stage["name"]]=audio[start:stop,:2]
    sw_meta=next(s for s in manifest["stages"] if s["name"]=="sweep")
    cfg=AcousticConfig(
        sample_rate=fs,start_hz=float(sw_meta["start_hz"]),
        end_hz=float(sw_meta["end_hz"]),
        sweep_seconds=float(sw_meta["sweep_seconds"]),
        level_dbfs=float(manifest["level_dbfs"]),
        tail_seconds=(sw_meta["frames"]-round(float(sw_meta["sweep_seconds"])*fs))/fs,
    )
    excitation=exponential_sweep(cfg)
    steps=next(s for s in manifest["stages"] if s["name"]=="thd_vs_frequency")
    levels=next(s for s in manifest["stages"] if s["name"]=="thd_vs_level")
    out={}
    for side,channel in (("left",0),("right",1)):
        fr=sweep_transfer(excitation,stage_map["sweep"][:,channel],
                          fs,calibration=mic_calibration,
                          lower_hz=cfg.start_hz,
                          upper_hz=min(cfg.end_hz,fs*.45))
        freq_rows=stepped_tone_analyze(stage_map["thd_vs_frequency"][:,channel],
                                        fs,steps["step_manifest"])
        lvl_manifest=levels["level_manifest"]
        lvl_audio=stage_map["thd_vs_level"][:,channel]
        lvl_waves=[
            lvl_audio[s["start_frame"]:s["start_frame"]+s["frames"]]
            for s in lvl_manifest
        ]
        lvl_rows=thd_level_curve(lvl_waves,fs,
             [item["level_dbfs"] for item in lvl_manifest],frequency_hz=levels.get("frequency_hz",997.))
        out[side]={"response":fr,"thd_frequency":freq_rows,"thd_level":lvl_rows}
    marker=_probe(fs,manifest["level_dbfs"],manifest["marker_frames"])
    mstart=lag+manifest["marker_offset_frames"]
    amp=[]
    for ch in (0,1):
        x=audio[mstart:mstart+len(marker),ch]
        amp.append(float(np.dot(x,marker)/np.dot(marker,marker)))
    mono=len(set(manifest.get('input_map',(1,2))))==1
    return {
      'channel_mode':'mono' if mono else 'stereo',
      "type":"rmaa-open-lab-acoustic-result", "fs":fs,
      "source":source,"created":datetime.now(timezone.utc).isoformat(),
      "alignment_frames":lag,"marker_similarity":score,"peak_input":peak,
      "left":out["left"],"right":{} if mono else out["right"],
      "calibration":{
         "marker_gain_db":[20*np.log10(max(abs(v),1e-15)) for v in (amp[:1] if mono else amp)],
         "polarity":["normal" if a>=0 else "inverted" for a in (amp[:1] if mono else amp)],
         "microphone_curve_applied":mic_calibration is not None,
      },
      "limitations":[
        "Acoustic response is relative to its reported reference_hz, not absolute SPL",
        "No mic sensitivity or sound-pressure reference calibration",
        "Clock drift and reflections require separately verified acoustic methodology",
        "Not validated against proprietary RMAA acoustic algorithms",
      ],
    }


def save_acoustic_files(folder, cfg:AcousticConfig):
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    wav,manifest=create_acoustic_sequence(cfg)
    wav_path=folder/"rmaa_acoustic_test.wav"
    meta_path=folder/"rmaa_acoustic_manifest.json"
    sf.write(str(wav_path),wav,cfg.sample_rate,subtype="PCM_24")
    meta_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return wav_path,meta_path


def import_acoustic_files(recording,manifest_file,mic_csv=None):
    if Path(manifest_file).stat().st_size>2_000_000:raise ValueError('Acoustic manifest exceeds 2 MB')
    manifest=json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    validate_acoustic_manifest(manifest)
    with sf.SoundFile(str(recording)) as infile:
        if infile.samplerate != int(manifest["fs"]):
            raise ValueError("Recording sample rate differs from generator")
        if infile.frames>25_000_000:
            raise ValueError("Recording too long; trim it first")
        samples=np.empty((infile.frames,2),dtype=np.float32)
        for start in range(0,infile.frames,65536):
            block=infile.read(frames=min(65536,infile.frames-start),dtype='float32',always_2d=True)
            samples[start:start+len(block)]=np.repeat(block,2,axis=1) if infile.channels==1 else block[:,:2]
        if infile.channels==1:
            manifest={**manifest,'input_map':[1,1]}
    calib=mic_calibration_from_csv(mic_csv) if mic_csv else None
    return analyze_acoustic_capture(samples,manifest,calib,source=str(recording))


def acoustic_live(input_device,output_device,cfg:AcousticConfig,mic_csv=None,
                  api_mode="Автоматически",format_label="32-bit float",cancel=None,stream_options=None,recording_path=None):
    from rmaa_capture import preflight_capture,save_capture
    if recording_path:preflight_capture(recording_path)
    source,manifest=create_acoustic_sequence(cfg)
    manifest.update(input_map=list((stream_options or {}).get('input_map',(1,2))),
                    output_map=list((stream_options or {}).get('output_map',(1,2))),
                    api_mode=api_mode,pcm_format=format_label)
    fs=cfg.sample_rate
    source=np.vstack((source,np.zeros((round(.7*fs),2),dtype=np.float32)))
    from rmaa_backend import playrec_with_format
    samples=playrec_with_format(source,fs,input_device,output_device,
            mode=api_mode,format_label=format_label,cancel=cancel,**(stream_options or {}))
    saved=save_capture(recording_path,samples,fs,manifest) if recording_path else {}
    calib=mic_calibration_from_csv(mic_csv) if mic_csv else None
    result=analyze_acoustic_capture(samples,manifest,calib,source="duplex sounddevice")
    result["api_mode"]=api_mode
    result["pcm_format"]=format_label
    result.update(saved)
    if len(set((stream_options or {}).get("input_map",(1,2))))==1:
        result["right"]={}
        result["channel_mode"]="mono"
        result["calibration"]["polarity"]=result["calibration"]["polarity"][:1]
        result["calibration"]["marker_gain_db"]=result["calibration"]["marker_gain_db"][:1]
    return result
