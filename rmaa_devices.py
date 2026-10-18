"""Safe device enumeration and capability probing without generating playback."""
from __future__ import annotations
import sounddevice as sd

RATES=(44100,48000,88200,96000,176400,192000,352800,384000)

def list_devices():
    devices=sd.query_devices()
    hostapis=sd.query_hostapis()
    inputs, outputs=[],[]
    for idx,d in enumerate(devices):
        entry={"id":idx,"name":d["name"],"host_api":hostapis[d["hostapi"]]["name"],
               "inputs":int(d["max_input_channels"]),"outputs":int(d["max_output_channels"]),
               "default_samplerate":float(d["default_samplerate"]),
               "default_low_input_latency":float(d["default_low_input_latency"]),
               "default_low_output_latency":float(d["default_low_output_latency"])}
        if entry["inputs"]>=2:inputs.append(entry)
        if entry["outputs"]>=2:outputs.append(entry)
    return {"inputs":inputs,"outputs":outputs,"host_apis":[h["name"] for h in hostapis]}

def probe_duplex(input_id,output_id,channels=2,rates=RATES,
                 format_label="32-bit float",mode="Автоматически",
                 input_map=(1,2),output_map=(1,2),**options):
    from rmaa_backend import PCM_FORMATS,extras_for_mode
    dtype=PCM_FORMATS[format_label]
    settings=extras_for_mode(input_id,output_id,mode)
    input_device=sd.query_devices(input_id)
    output_device=sd.query_devices(output_id)
    if input_device["max_input_channels"]<max(input_map):
        raise ValueError("Input device does not provide requested number of channels")
    if output_device["max_output_channels"]<max(output_map):
        raise ValueError("Output device does not provide requested number of channels")
    results=[]
    for fs in rates:
        inp, outp=None,None
        try:
            sd.check_input_settings(device=input_id,channels=max(input_map),samplerate=fs,dtype=dtype,extra_settings=settings[0] if settings else None)
            inp=True
        except Exception as ex:
            inp=str(ex).splitlines()[0][:140]
        try:
            sd.check_output_settings(device=output_id,channels=max(output_map),samplerate=fs,dtype=dtype,extra_settings=settings[1] if settings else None)
            outp=True
        except Exception as ex:
            outp=str(ex).splitlines()[0][:140]
        results.append({"fs":fs,"input":inp,"output":outp,"duplex_possible":inp is True and outp is True})
    return {
        "input_name":input_device["name"], "output_name":output_device["name"],
        "channels":channels,"dtype":dtype,
        "rates":results,
        "note":"Capability probe only; a successful setting check cannot prove stable recording, exclusive mode, or bit-perfect I/O.",
    }
