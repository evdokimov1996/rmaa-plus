"""Independent WAV spectrum analyzer.

Spectral amplitude values are *peak-referred dBFS for coherent sine tones*,
NOT RMS level and NOT RMAA's proprietary calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

_EPS = np.finfo(np.float64).tiny

@dataclass(frozen=True)
class SpectrumOptions:
    fft_size: int = 16384
    overlap_percent: float = 75.0
    window: str = "kaiser"
    kaiser_beta: float = 9.0
    zero_padding: int = 1

    def validate(self):
        if not 1024 <= self.fft_size <= 8_388_608 or self.fft_size & (self.fft_size - 1):
            raise ValueError("FFT size must be a power of 2 between 1024 and 8,388,608")
        if self.overlap_percent not in (0, 25, 50, 75, 87.5):
            raise ValueError("Overlap must be 0, 25, 50, 75 or 87.5 percent")
        if self.window not in ("hann", "hamming", "blackman", "blackmanharris", "kaiser", "boxcar"):
            raise ValueError("Unsupported FFT window")
        if self.zero_padding not in (1, 2, 4):
            raise ValueError("Zero padding must be 1, 2 or 4")
        if self.fft_size * self.zero_padding > 16_777_216:
            raise ValueError("FFT × zero-padding too large; reduce options")
        if not (0 <= self.kaiser_beta <= 30):
            raise ValueError("Kaiser beta out of range 0..30")


def audio_file_info(path: str | Path):
    info = sf.info(str(path))
    return {
        "samplerate": info.samplerate, "channels": info.channels,
        "frames": info.frames, "duration_seconds": info.duration,
        "subtype": info.subtype, "format": info.format,
    }


def read_wav_segment(path, fft_size, max_frames=10_000_000,channel=0):
    """Read one selected channel in bounded blocks, at most 10M frames."""
    opts = audio_file_info(path)
    if opts["format"] not in ("WAV", "WAVEX", "RF64"):
        raise ValueError("This function analyzes WAV/RF64 files only")
    if opts["frames"] < fft_size:
        raise ValueError(f"Audio too short for FFT {fft_size}: {opts['frames']} frames")
    if not 0<=channel<opts['channels']:raise ValueError(f'Channel {channel+1} not present')
    with sf.SoundFile(str(path)) as file:
        frames = min(file.frames, max(max_frames, fft_size))
        if frames > max_frames:
            raise ValueError("FFT exceeds configured analysis limit")
        values=np.empty(frames,dtype=np.float64)
        chunk=max(1,min(65536,1048576//file.channels))
        for start in range(0,frames,chunk):
            block=file.read(frames=min(chunk,frames-start),dtype='float64',always_2d=True)
            values[start:start+len(block)]=block[:,channel]
    if not np.isfinite(values).all():
        raise ValueError("WAV contains NaN or Infinity")
    return values, int(opts["samplerate"]), opts


def compute_spectrum(x, samplerate: int, opts: SpectrumOptions):
    """Average window-corrected FFT amplitude *power* across windows.

    At an FFT bin centered on a 0.5 peak-amplitude sine, displays ~-6.02 dBFS.
    Noncoherent sinusoids can have scalloping loss; no peak interpolation here.
    """
    opts.validate()
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError("Expected one audio channel")
    if arr.size < opts.fft_size:
        raise ValueError("Audio shorter than FFT window")
    if samplerate < 8000:
        raise ValueError("Unsupported sample rate")
    arr = arr - float(np.mean(arr))
    if opts.window == "kaiser":
        win = signal.windows.kaiser(opts.fft_size, beta=opts.kaiser_beta, sym=False)
    else:
        win = signal.windows.get_window(opts.window, opts.fft_size, fftbins=True)
    coherent_sum = float(np.sum(win))
    if coherent_sum <= 0:
        raise ValueError("Invalid window")
    step = max(1, int(round(opts.fft_size * (1.0 - opts.overlap_percent / 100.0))))
    nfft = opts.fft_size * opts.zero_padding
    accum = np.zeros(nfft // 2 + 1, dtype=np.float64)
    count = 0
    for start in range(0, arr.size - opts.fft_size + 1, step):
        segment = arr[start:start + opts.fft_size] * win
        spec = np.fft.rfft(segment, n=nfft)
        amplitude = (2.0 / coherent_sum) * np.abs(spec)
        amplitude[0] *= .5
        if nfft % 2 == 0:
            amplitude[-1] *= .5
        accum += amplitude * amplitude
        count += 1
        # Avoid computing thousands of huge FFTs when user selects very high overlap.
        if count >= 4000:
            break
    avg_amp = np.sqrt(accum / max(count, 1))
    freqs = np.fft.rfftfreq(nfft, 1.0 / samplerate)
    levels = 20 * np.log10(np.maximum(avg_amp, _EPS))
    levels = np.clip(levels, -240.0, 80.0)
    mask = (freqs > 0) & (freqs <= samplerate / 2)
    if not np.any(mask):
        raise ValueError("No frequency bins")
    peak_index = int(np.argmax(avg_amp[mask]))
    peak_frequency = float(freqs[mask][peak_index])
    peak_dbfs = float(levels[mask][peak_index])
    return {
        "frequency_hz": freqs,
        "amplitude_dbfs_peak": levels,
        "fft_size": opts.fft_size,
        "window_count": count,
        "peak_frequency_hz": peak_frequency,
        "peak_amplitude_dbfs": peak_dbfs,
        "resolution_hz": float(samplerate / opts.fft_size),
        "frequency_spacing_hz": float(samplerate / nfft),
        "audio_rms_dbfs": float(20*np.log10(max(float(np.sqrt(np.mean(arr * arr))), _EPS))),
    }


def spectrum_wav(path, opts: SpectrumOptions, channel=0):
    data, samplerate, metadata = read_wav_segment(path, opts.fft_size,channel=channel)
    result = compute_spectrum(data, samplerate, opts)
    result["metadata"] = metadata
    result["channel"] = channel
    result["path"] = str(path)
    return result


def hz_to_mel(frequencies):
    f = np.asarray(frequencies, dtype=float)
    return 2595.0 * np.log10(1.0 + np.maximum(f, 0.0) / 700.0)


def mel_to_hz(mels):
    m = np.asarray(mels, dtype=float)
    return 700.0 * (np.power(10.0, m / 2595.0) - 1.0)


def dbfs_to_label(value):
    return f"{float(value):.2f} dBFS (peak-ref)"
