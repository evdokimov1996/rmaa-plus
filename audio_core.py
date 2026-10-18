"""Open audio-measurement core; independent implementation, no RMAA code.

All readings are exploratory until hardware loopback and calibration checks are done.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal

EPS = 1e-15


def db20(value):
    return 20.0 * math.log10(max(float(value), EPS))


def rms(samples):
    x = np.asarray(samples, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def generate_tone(fs=48000, frequency=997.0, seconds=1.2, level_dbfs=-18.0):
    """Stereo full-scale-referenced tone; low default level to protect equipment."""
    t = np.arange(round(fs * seconds), dtype=np.float64) / fs
    tone = (10.0 ** (level_dbfs / 20.0)) * np.sin(2.0 * np.pi * frequency * t)
    return np.column_stack((tone, tone)).astype(np.float32)


def generate_sweep(fs=48000, seconds=4.0, level_dbfs=-18.0):
    """Stereo logarithmic sweep with 30 ms fades."""
    n = round(fs * seconds)
    t = np.arange(n, dtype=np.float64) / fs
    top = min(20000.0, 0.44 * fs)
    s = signal.chirp(t, f0=20.0, f1=top, t1=seconds, method="logarithmic")
    fade = min(round(0.03 * fs), n // 4)
    if fade:
        window = np.ones(n)
        window[:fade] = np.linspace(0, 1, fade, endpoint=True)
        window[-fade:] = np.linspace(1, 0, fade, endpoint=True)
        s *= window
    s *= 10.0 ** (level_dbfs / 20.0)
    return np.column_stack((s, s)).astype(np.float32)


def fit_components(x, fs, frequencies):
    """Least-squares sine/cosine fit, robust to noninteger FFT bin frequencies."""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    t = np.arange(x.size, dtype=np.float64) / fs
    cols = [np.ones(x.size)]
    for freq in frequencies:
        cols.append(np.sin(2 * np.pi * freq * t))
        cols.append(np.cos(2 * np.pi * freq * t))
    design = np.column_stack(cols)
    coefficients, *_ = np.linalg.lstsq(design, x, rcond=None)
    amps = [
        float(np.hypot(coefficients[1 + 2 * i], coefficients[2 + 2 * i]))
        for i in range(len(frequencies))
    ]
    return amps, (x - design @ coefficients), float(coefficients[0])


def analyze_tone(x, fs=48000, frequency=997.0, max_harmonic=9):
    """THD by harmonic least squares; THD+N by 1 kHz fundamental subtraction."""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    if x.size < fs // 5:
        raise ValueError("Not enough signal for tone analysis")
    # Discard early device transients.
    x = x[min(x.size // 10, int(0.08 * fs)):]
    h = [frequency * k for k in range(1, max_harmonic + 1) if frequency * k < fs * 0.48]
    amps, _, dc = fit_components(x, fs, h)
    base = max(amps[0], EPS)
    thd = 100.0 * math.sqrt(sum(a * a for a in amps[1:])) / base
    _, remainder, _ = fit_components(x, fs, [frequency])
    thdn = 100.0 * math.sqrt(2.0) * rms(remainder) / base
    return {
        "fundamental_hz": frequency,
        "fundamental_dbfs": db20(base),
        "thd_pct": float(thd),
        "thdn_pct": float(thdn),
        "thdn_db": db20(max(thdn / 100.0, EPS)),
        "dc_offset_pct": 100.0 * dc,
        "harmonics_db": [db20(a / base) for a in amps[1:]],
    }


def analyze_noise(x):
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    mean = float(np.mean(x))
    ac = x - mean
    return {
        "noise_dbfs_rms": db20(rms(ac)),
        "peak_dbfs": db20(np.max(np.abs(x))),
        "dc_offset_pct": mean * 100.0,
    }


def find_delay(x, y, fs):
    """FFT correlation to find recording latency within +-0.4 seconds."""
    n = min(len(x), len(y))
    a = np.asarray(x[:n], dtype=np.float64)
    b = np.asarray(y[:n], dtype=np.float64)
    downsample = max(1, fs // 12000)
    a = a[::downsample]
    b = b[::downsample]
    cc = signal.correlate(b, a, method="fft", mode="full")
    lags = signal.correlation_lags(len(b), len(a), mode="full")
    allowed = np.abs(lags) <= round(0.4 * fs / downsample)
    idx = int(np.argmax(np.abs(cc[allowed])))
    delay = int(lags[allowed][idx]) * downsample
    return delay


def analyze_frequency_response(reference, recorded, fs, low=40.0, high=15000.0):
    """Swept-tone transfer magnitude; normalized at 1 kHz. Exploratory."""
    a = np.asarray(reference, dtype=np.float64).reshape(-1)
    b = np.asarray(recorded, dtype=np.float64).reshape(-1)
    lag = find_delay(a, b, fs)
    if lag >= 0:
        a = a[:max(0, len(a) - lag)]
        b = b[lag:lag + len(a)]
    else:
        a = a[-lag:]
        b = b[:len(a)]
    n = min(len(a), len(b))
    if n < fs // 2:
        raise ValueError("Too little aligned audio")
    a = a[:n]
    b = b[:n]
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    af = np.fft.rfft(a)
    bf = np.fft.rfft(b)
    mag = np.abs(bf) / np.maximum(np.abs(af), EPS)
    # Smooth magnitude in log-frequency bands.
    freqs_out = np.geomspace(20, min(20000, fs * 0.45), 120)
    curve = []
    for f in freqs_out:
        band = (freqs > f / 1.023) & (freqs < f * 1.023)
        if not np.any(band):
            band = np.abs(freqs - f) == np.min(np.abs(freqs - f))
        curve.append(float(np.median(mag[band])))
    curve = np.asarray(curve)
    ref_1k = float(np.interp(1000, freqs_out, curve))
    db = 20.0 * np.log10(np.maximum(curve, EPS) / max(ref_1k, EPS))
    mask = (freqs_out >= low) & (freqs_out <= high)
    return {
        "band_hz": [low, high],
        "min_db": float(np.min(db[mask])),
        "max_db": float(np.max(db[mask])),
        "latency_ms": lag * 1000.0 / fs,
        "frequency_hz": np.round(freqs_out, 2).tolist(),
        "response_db": np.round(db, 3).tolist(),
    }


def check_audio_devices():
    import sounddevice as sd
    devices = sd.query_devices()
    inputs = [{"id": i, "name": d["name"], "channels": d["max_input_channels"]}
              for i, d in enumerate(devices) if d["max_input_channels"] >= 2]
    outputs = [{"id": i, "name": d["name"], "channels": d["max_output_channels"]}
               for i, d in enumerate(devices) if d["max_output_channels"] >= 2]
    return inputs, outputs


def acquire(output, fs, input_device, output_device, tail_seconds=0.3):
    import sounddevice as sd
    if not (-36 <= db20(max(np.abs(output).max(), EPS)) <= -3):
        raise ValueError("Playback must be between -36 and -3 dBFS peak")
    tail = np.zeros((round(tail_seconds * fs), 2), dtype=np.float32)
    samples = np.vstack((output, tail))
    # Explicit duplex devices. No system-volume manipulation.
    recorded = sd.playrec(
        samples, samplerate=fs, channels=2, dtype="float32",
        device=(input_device, output_device), blocking=True
    )
    return np.asarray(recorded, dtype=np.float64)


def run_suite(input_device, output_device, fs=48000, dbfs=-18.0):
    if not (-36 <= dbfs <= -6):
        raise ValueError("Requested level outside safe test range (-36 to -6 dBFS)")
    tone = generate_tone(fs, seconds=1.2, level_dbfs=dbfs)
    tone_rec = acquire(tone, fs, input_device, output_device)
    # Offset-compensate before cropping tone.
    lag = max(0, find_delay(tone[:, 0], tone_rec[:, 0], fs))
    tone_samples = tone_rec[lag:lag + len(tone)]
    if len(tone_samples) < round(0.8 * fs):
        raise ValueError("Not enough recorded samples; check loopback")
    noise = np.zeros((fs, 2), dtype=np.float32)
    noise_rec = acquire(noise + 0.0, fs, input_device, output_device) if False else None
    # Silence acquisition must not call acquire(), which checks nonzero output peak.
    import sounddevice as sd
    noise_rec = sd.playrec(
        np.zeros((round(1.2 * fs), 2), dtype=np.float32),
        samplerate=fs, channels=2, device=(input_device, output_device),
        dtype="float32", blocking=True
    )
    noise_rec = np.asarray(noise_rec[int(fs * 0.1):], dtype=np.float64)
    sweep = generate_sweep(fs, seconds=3.0, level_dbfs=dbfs)
    sweep_rec = acquire(sweep, fs, input_device, output_device)
    resp = [
        analyze_frequency_response(sweep[:, c], sweep_rec[:, c], fs)
        for c in range(2)
    ]
    return {
        "label": "Preliminary audio loopback measurements (not RMAA-certified)",
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "fs": fs,
        "level_dbfs": dbfs,
        "input_device": input_device,
        "output_device": output_device,
        "left": {"tone": analyze_tone(tone_samples[:, 0], fs),
                 "noise": analyze_noise(noise_rec[:, 0]),
                 "response": resp[0]},
        "right": {"tone": analyze_tone(tone_samples[:, 1], fs),
                  "noise": analyze_noise(noise_rec[:, 1]),
                  "response": resp[1]},
        "limitations": [
            "Preliminary uncalibrated measurements; not numerically equivalent to RMAA.",
            "Use a real line-out-to-line-in cable; microphones are not calibrated loopback inputs.",
            "IMD, A-weighting, dynamic range and crosstalk are not implemented yet.",
            "Input and output must support the selected common stereo sample rate.",
            "Audio levels and driver configuration strongly affect results.",
        ],
    }


def save_json(path, results):
    Path(path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
