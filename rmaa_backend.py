"""PortAudio stream options for RMAA Open Lab.

ASIO is usable only when the PortAudio build and an installed ASIO driver
actually expose an ASIO host API. WASAPI Exclusive is explicitly requested
and rejects non-WASAPI device combinations.

Raw 24-bit signed little-endian samples are packed and decoded without first
truncating to 16 bits. This does not prove a DAC/ADC is bit-perfect.
"""
from __future__ import annotations
from threading import Event
import numpy as np
import sounddevice as sd

PCM_FORMATS={
    "32-bit float":"float32",
    "8-bit PCM":"uint8",
    "16-bit PCM":"int16",
    "24-bit PCM":"int24",
    "32-bit PCM":"int32",
}
MODES=("Автоматически","Automatic","MME","DirectSound","WASAPI Shared","WASAPI Exclusive","ASIO","Kernel Streaming")


def show_asio_control_panel(device_id,window_handle=0):
    """Open the selected installed driver's native panel on the UI thread."""
    if "ASIO" not in api_for_device(device_id):
        raise ValueError("Select an ASIO device first")
    import ctypes
    library=ctypes.CDLL(sd._libname)
    try:show=library.PaAsio_ShowControlPanel
    except AttributeError as exc:raise RuntimeError("This PortAudio build has no ASIO control panel API") from exc
    show.argtypes=(ctypes.c_int,ctypes.c_void_p)
    show.restype=ctypes.c_int
    code=show(int(device_id),ctypes.c_void_p(int(window_handle)))
    if code<0:raise RuntimeError(f"ASIO control panel: {sd._ffi.string(sd._lib.Pa_GetErrorText(code)).decode(errors='replace')}")


def api_for_device(device_id):
    device=sd.query_devices(int(device_id))
    return sd.query_hostapis(int(device["hostapi"]))["name"]


def validate_mode(input_id,output_id,mode):
    if mode not in MODES:
        raise ValueError("Unknown audio API mode")
    ins=api_for_device(input_id)
    outs=api_for_device(output_id)
    if mode=="WASAPI Exclusive":
        if "WASAPI" not in ins or "WASAPI" not in outs:
            raise ValueError("WASAPI Exclusive requires WASAPI input and output devices")
        if not hasattr(sd,"WasapiSettings"):
            raise RuntimeError("PortAudio build lacks WASAPI settings")
    if mode=="ASIO" and input_id!=output_id:
        raise ValueError("ASIO duplex requires one common driver/device; use WASAPI for independent devices")
    if mode=="ASIO" and ("ASIO" not in ins or "ASIO" not in outs):
        raise ValueError("ASIO requires input/output devices exposed by the ASIO host API")
    if mode=="Kernel Streaming" and ("WDM-KS" not in ins or "WDM-KS" not in outs):
        raise ValueError("Kernel Streaming requires WDM-KS devices")
    for label, token in (("MME","MME"),("DirectSound","DirectSound"),("WASAPI Shared","WASAPI")):
        if mode == label and (token not in ins or token not in outs):
            raise ValueError(f"{mode} requires matching input/output host APIs")
    if ins != outs:
        raise ValueError("Input and output must use the same host API")
    return ins,outs


def extras_for_mode(input_id,output_id,mode):
    validate_mode(input_id,output_id,mode)
    if mode=="WASAPI Exclusive":
        return (sd.WasapiSettings(exclusive=True),
                sd.WasapiSettings(exclusive=True))
    return None


def pack_int24(audio):
    """Signed 24-bit little-endian interleaved frames from floating [-1,1]."""
    x=np.asarray(audio,dtype=np.float64)
    if x.ndim != 2 or x.shape[1] < 1 or not np.isfinite(x).all():
        raise ValueError("Expected finite [frames, channels] PCM samples")
    samples=np.clip(np.rint(x*8388608),-8388608,8388607).astype("<i4")
    bytes4=samples.view(np.uint8).reshape(-1,4)
    return bytes4[:,:3].ravel().tobytes()


def unpack_int24(blob,channels=2):
    data=np.frombuffer(blob,dtype=np.uint8)
    if channels<=0 or len(data)%(3*channels):
        raise ValueError("Packed PCM byte length is not divisible by frame size")
    t=data.reshape(-1,3).astype(np.int32)
    vals=t[:,0] | (t[:,1]<<8) | (t[:,2]<<16)
    vals=(vals ^ 0x800000) - 0x800000
    return (vals.astype(np.float64).reshape(-1,channels)/8388608.0).astype(np.float32)


def _float_to_integer(samples,dtype):
    x=np.asarray(samples,dtype=np.float64)
    if dtype=="int16":
        return np.clip(np.rint(x*32768),-32768,32767).astype(np.int16)
    if dtype=="int32":
        return np.clip(np.rint(x*2147483648),-2147483648,2147483647).astype(np.int32)
    if dtype=="float32":
        return x.astype(np.float32)
    raise ValueError("Invalid conversion datatype")


def _stream_record_raw24(audio,fs,devices,extra_settings=None):
    """Full-duplex callback RawStream, packed 24-bit samples.

    Cannot be considered operational on real hardware until tested with a
    driver that supports PaInt24. Timeout guards against stuck audio drivers.
    """
    x=np.asarray(audio,dtype=np.float64)
    if x.ndim != 2 or x.shape[1]!=2:
        raise ValueError("24-bit stream currently requires stereo")
    playback=pack_int24(x)
    samples_total=len(x)
    received=[]
    completed=Event()
    state={"idx":0,"error":None}
    channels=2
    stride=3*channels

    def callback(indata,outdata,frames,time,status):
        try:
            i=state["idx"]
            samples=min(frames,max(0,samples_total-i))
            payload=playback[i*stride:(i+samples)*stride]
            # Exact sized output buffer; pad trailing frames with digital silence.
            outdata[:]=payload + bytes(max(0,frames-samples)*stride)
            received.append(bytes(indata))
            state["idx"]=i+frames
            if state["idx"]>=samples_total:
                raise sd.CallbackStop
        except sd.CallbackStop:
            raise
        except Exception as err:
            state["error"]=str(err)
            raise sd.CallbackAbort

    kwargs={
      "samplerate":fs,"channels":(channels,channels),
      "device":devices,"dtype":"int24","callback":callback,
      "finished_callback":completed.set,"blocksize":0,
    }
    if extra_settings is not None:kwargs["extra_settings"]=extra_settings
    timeout=max(20.0,2.5*len(x)/fs+10)
    with sd.RawStream(**kwargs):
        if not completed.wait(timeout):
            raise TimeoutError("24-bit full-duplex raw stream timeout")
    if state["error"]:
        raise RuntimeError("Audio callback failure: "+state["error"])
    # A callback may request a tail block; crop to the intended recording length.
    data=unpack_int24(b"".join(received),2)
    if len(data)<samples_total:
        raise RuntimeError("Short 24-bit recording")
    return data[:samples_total]


def playrec_with_format(data,fs,input_id,output_id,
                        mode="Автоматически",format_label="32-bit float", **options):
    """Explicit format; private stream, cancellation and channel routing."""
    from rmaa_transport import transfer,ensure_session_available
    ensure_session_available()
    if format_label not in PCM_FORMATS:
        raise ValueError("Unsupported requested sample format")
    if fs<8000 or fs>384000:
        raise ValueError("Invalid sample rate")
    settings=extras_for_mode(input_id,output_id,mode)
    dtype=PCM_FORMATS[format_label]
    for direction, dev, mapping, extra in (
        (sd.check_input_settings,input_id,options.get("input_map",(1,2)),settings[0] if settings else None),
        (sd.check_output_settings,output_id,options.get("output_map",(1,2)),settings[1] if settings else None)):
        direction(device=dev,channels=max(mapping),samplerate=fs,dtype=dtype,extra_settings=extra)
    return transfer(data,fs,input_id,output_id,dtype=dtype,extra_settings=settings,**options)



def installed_api_capabilities():
    host_names=[api["name"] for api in sd.query_hostapis()]
    return {
      "host_apis":host_names,
      "asio_present":any("ASIO" in x for x in host_names),
      "wasapi_present":any("WASAPI" in x for x in host_names),
      "kernel_streaming_present":any("WDM-KS" in x for x in host_names),
      "pcm_formats":list(PCM_FORMATS),
      "exclusive_requires_matching_wasapi_devices":True,
    }
