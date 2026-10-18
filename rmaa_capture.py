"""Save a completed capture before analysis, without replacing existing files."""
import json
from pathlib import Path
import soundfile as sf


def preflight_capture(path):
    location=Path(path)
    if location.suffix.lower()!='.wav':raise ValueError('Capture filename must end in .wav')
    if not location.parent.is_dir() or location.exists() or location.with_suffix('.json').exists():
        raise FileExistsError('Choose a new capture/manifest filename in an existing directory')
    return location


def save_capture(path,samples,fs,manifest):
    location=preflight_capture(path)
    meta=location.with_suffix('.json')
    # Exclusive creation also protects against a filename collision after playback.
    # A completed WAV survives even if writing the companion manifest fails.
    with location.open('xb') as file:sf.write(file,samples,fs,subtype='FLOAT',format='WAV')
    with meta.open('x',encoding='utf8') as file:json.dump(manifest,file,ensure_ascii=False,indent=2)
    return {'saved_recording':str(location),'saved_manifest':str(meta)}
