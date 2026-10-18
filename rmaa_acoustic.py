"""Independent acoustic and stepped-frequency tests for RMAA Open Lab.

All curves are relative; microphone correction files do not provide absolute SPL.
The sweep analysis uses regularized FFT deconvolution, not the undisclosed RMAA method.
"""
from __future__ import annotations
import csv
import math
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from scipy import signal
from scipy.fft import next_fast_len

from rmaa_dsp import tone_metrics, db

_EPS = 1e-15

@dataclass(frozen=True)
class AcousticConfig:
    sample_rate: int = 48000
    start_hz: float = 20.0
    end_hz: float = 20000.0
    sweep_seconds: float = 4.0
    level_dbfs: float = 0.0
    tail_seconds: float = 0.5
    detail: str = "standard"

    def validate(self):
        if self.detail not in ("standard","detailed","thorough","subwoofer"):
            raise ValueError("Unknown acoustic detail preset")
        if not 32000 <= self.sample_rate <= 384000:
            raise ValueError("Sample rate must be 32–384 kHz")
        if not (10 <= self.start_hz < self.end_hz < self.sample_rate * 0.46):
            raise ValueError("Invalid sweep frequency range")
        if not 1 <= self.sweep_seconds <= 30:
            raise ValueError("Sweep duration must be 1–30 seconds")
        if not -50 <= self.level_dbfs <= 0:
            raise ValueError("Test level must be between -50 and 0 dBFS")
        if not 0.0 <= self.tail_seconds <= 5:
            raise ValueError("Tail must be 0–5 s")


def exponential_sweep(cfg: AcousticConfig):
    """Logarithmic sine sweep with 50 ms fades and optional silent tail."""
    cfg.validate()
    fs = cfg.sample_rate
    n = round(cfg.sweep_seconds * fs)
    t = np.arange(n, dtype=np.float64) / fs
    ratio = math.log(cfg.end_hz / cfg.start_hz)
    phase = (2 * np.pi * cfg.start_hz * cfg.sweep_seconds / ratio) * np.expm1(t * ratio / cfg.sweep_seconds)
    wave = np.sin(phase)
    fade = min(round(fs * .05), n // 8)
    window = np.ones(n)
    window[:fade] = np.sin(np.linspace(0, np.pi/2, fade)) ** 2
    window[-fade:] = np.cos(np.linspace(0, np.pi/2, fade)) ** 2
    wave *= window * 10 ** (cfg.level_dbfs / 20.0)
    return np.concatenate([wave, np.zeros(round(cfg.tail_seconds * fs))]).astype(np.float32)


def stereo_exponential_sweep(cfg: AcousticConfig):
    wave = exponential_sweep(cfg)
    return np.column_stack((wave, wave))


def mic_calibration_from_csv(path):
    """CSV rows: frequency_hz, correction_db. Extra columns are ignored."""
    frequencies, corrections = [], []
    with open(Path(path), newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            try:
                freq = float(row[0])
                correction = float(row[1])
            except ValueError:
                if not frequencies:
                    continue
                raise ValueError("Malformed microphone correction CSV")
            if not np.isfinite(freq) or not np.isfinite(correction) or freq <= 0:
                raise ValueError("Non-finite/invalid calibration data")
            frequencies.append(freq)
            corrections.append(correction)
    if len(frequencies) < 2:
        raise ValueError("At least two calibration points required")
    freq = np.asarray(frequencies)
    dbs = np.asarray(corrections)
    if not np.all(np.diff(freq) > 0):
        raise ValueError("Mic calibration frequencies must increase")
    if np.any(np.abs(dbs) > 60):
        raise ValueError("Unrealistic microphone correction exceeds 60 dB")
    return {"frequency_hz": freq.tolist(), "correction_db": dbs.tolist()}


def corrected_curve(frequencies, magnitudes_db, calibration=None):
    """Adds log-interpolated microphone compensation, clamped at endpoint values."""
    frequencies = np.asarray(frequencies, dtype=float)
    curve = np.asarray(magnitudes_db, dtype=float)
    if calibration is None:
        return curve
    f = np.asarray(calibration["frequency_hz"], dtype=float)
    c = np.asarray(calibration["correction_db"], dtype=float)
    if len(f) < 2 or not np.all(np.diff(f) > 0):
        raise ValueError("Invalid calibration")
    return curve + np.interp(
        np.log(np.maximum(frequencies, 1.0)), np.log(f), c, left=c[0], right=c[-1]
    )


def _estimate_delay(reference, captured, fs, max_delay_seconds):
    reference = np.asarray(reference, dtype=np.float64)
    captured = np.asarray(captured, dtype=np.float64)
    if len(captured) < len(reference) // 2 or len(reference) < fs:
        raise ValueError("Insufficient sweep signal")
    stride = max(1, fs // 12000)
    a = reference[::stride]
    b = captured[::stride]
    cc = signal.correlate(b, a, mode="full", method="fft")
    lag = signal.correlation_lags(len(b), len(a), mode="full")
    allowed = (lag >= -round(.1*fs/stride)) & (lag <= round(max_delay_seconds*fs/stride))
    if not np.any(allowed):
        raise ValueError("No valid latency range")
    delay = int(lag[allowed][np.argmax(np.abs(cc[allowed]))]) * stride
    return delay


def sweep_transfer(reference, recorded, fs, calibration=None, lower_hz=30, upper_hz=None):
    """Latency-align and estimate regularized transfer function.

    Frequency response and phase are only valid where the excitation spectrum
    is strong enough; ratio is regularized and smoothed by log-frequency bins.
    """
    a = np.asarray(reference, dtype=np.float64)
    b = np.asarray(recorded, dtype=np.float64)
    if a.ndim != 1 or b.ndim != 1 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Expected finite mono audio")
    if len(a) < fs or len(b) < fs:
        raise ValueError("At least one second of audio required")
    delay = _estimate_delay(a, b, fs, .75)
    if delay >= 0:
        x = a[:min(len(a), len(b)-delay)]
        y = b[delay:delay+len(x)]
    else:
        x = a[-delay:]
        y = b[:len(x)]
    n = min(len(x), len(y))
    if n < fs:
        raise ValueError("Recording too short after alignment")
    x = x[:n]
    y = y[:n]
    nfft = next_fast_len(2*n)
    fx = np.fft.rfft(x, nfft)
    fy = np.fft.rfft(y, nfft)
    power = np.square(np.abs(fx))
    # Tikhonov regularization proportional to in-band peak power.
    regularizer = max(float(np.max(power)) * 1e-13, _EPS)
    h = fy * np.conj(fx) / (power + regularizer)
    freqs = np.fft.rfftfreq(nfft, 1/fs)
    upper = min(upper_hz or 20000, fs * .45)
    if lower_hz < 10 or upper <= lower_hz:
        raise ValueError("Invalid analysis bandwidth")
    plot_f = np.geomspace(lower_hz, upper, 240)
    valid = freqs > 0
    logmag = 20*np.log10(np.maximum(np.abs(h[valid]), _EPS))
    phase = np.unwrap(np.angle(h[valid]))
    smoothed = np.empty(len(plot_f))
    smoothed_phase = np.empty(len(plot_f))
    for i, f in enumerate(plot_f):
        near = (freqs[valid] > f/1.035) & (freqs[valid] < f*1.035)
        if np.any(near):
            smoothed[i] = float(np.median(logmag[near]))
            smoothed_phase[i] = float(np.median(phase[near]))
        else:
            smoothed[i] = np.interp(f, freqs[valid], logmag)
            smoothed_phase[i] = np.interp(f, freqs[valid], phase)
    reference_hz=min(1000.,upper/2) if upper<1000 else 1000.
    reference_hz=max(lower_hz,reference_hz)
    normalized = smoothed - float(np.interp(reference_hz, plot_f, smoothed))
    compensated = corrected_curve(plot_f, normalized, calibration)
    impulse = np.fft.irfft(h, nfft)
    peak_idx = int(np.argmax(np.abs(impulse[:min(n, fs//2)])))
    return {
        "frequency_hz": plot_f.tolist(),
        "reference_hz": reference_hz,
        "response_db": np.round(compensated, 4).tolist(),
        "phase_deg": np.round(smoothed_phase * 180 / np.pi, 4).tolist(),
        "latency_samples": delay,
        "latency_ms": delay * 1000.0/fs,
        "min_db": float(np.min(compensated)),
        "max_db": float(np.max(compensated)),
        "impulse_peak_index": peak_idx,
        "impulse_first_4096": np.round(impulse[:4096], 7).tolist(),
        "microphone_calibrated_relative": calibration is not None,
        "method": "regularized FFT transfer with log-frequency-bin median smoothing",
        "warning": "Not equivalent to RMAA proprietary acoustic measurement; no absolute SPL calibration.",
    }


def thd_frequency_curve(recordings, fs, frequencies, skip_seconds=.06):
    """THD vs frequency for discrete known sine steps of equal durations."""
    if len(recordings) != len(frequencies):
        raise ValueError("Must have one recording for every frequency")
    values = []
    for wave, f in zip(recordings, frequencies):
        wave = np.asarray(wave, dtype=float)
        skip = round(skip_seconds*fs)
        if len(wave) < max(fs//3, skip*3):
            raise ValueError("Step too short")
        if f <= 0 or f >= .45*fs:
            raise ValueError("Invalid test frequency")
        result = tone_metrics(wave[skip:], fs, f=f, max_harmonic=12)
        values.append({"frequency_hz": float(f), **result})
    return values


def thd_level_curve(recordings, fs, levels_dbfs, frequency_hz=997):
    """THD vs nominal generator level, with no absolute electrical calibration."""
    if len(recordings) != len(levels_dbfs):
        raise ValueError("Each level needs a separate captured recording")
    result = []
    for level, wave in zip(levels_dbfs, recordings):
        if level > 0 or level < -90:
            raise ValueError("Generator level out of range")
        values = tone_metrics(np.asarray(wave, dtype=float), fs, f=frequency_hz)
        result.append({"nominal_level_dbfs": float(level), **values})
    return result


def stepped_tone_sequence(fs, frequencies=(40,80,160,315,630,1000,2000,4000,8000,12000),
                          level_dbfs=0, stage_seconds=.75, gap_seconds=.12):
    """Return concatenated stepped tones and manifest, no sound played."""
    if fs < 32000 or not -50 <= level_dbfs <= 0:
        raise ValueError("Invalid fs or level")
    from rmaa_dsp import tone
    chunks, meta = [], []
    offset = 0
    for f in frequencies:
        if f >= .44*fs or f <= 0:
            raise ValueError("Tone is above Nyquist-safe range")
        stage = tone(fs, f=f, seconds=stage_seconds, peak_dbfs=level_dbfs)
        chunks.append(stage)
        meta.append({"frequency_hz": float(f), "start_frame": offset, "frames": len(stage)})
        gap = np.zeros(round(fs*gap_seconds), dtype=np.float32)
        chunks.append(gap)
        offset += len(stage)+len(gap)
    return np.concatenate(chunks), meta


def stepped_tone_analyze(recorded, fs, manifest, lag_samples=0):
    waves=[]
    for item in manifest:
        start=int(item["start_frame"]+lag_samples)
        end=start+int(item["frames"])
        if start < 0 or end > len(recorded):
            raise ValueError("Incomplete captured stepped tone sequence")
        waves.append(np.asarray(recorded[start:end], dtype=float))
    return thd_frequency_curve(waves, fs, [m["frequency_hz"] for m in manifest])
