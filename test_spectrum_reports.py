"""Synthetic, offline tests of WAV spectral analysis and HTML/TXT reporting."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from spectrum_tools import SpectrumOptions, spectrum_wav, hz_to_mel, mel_to_hz, compute_spectrum
from rmaa_reports import report_html, report_txt, load_result_json


class SpectrumTests(unittest.TestCase):
    def test_spectrum_bin_sine_amplitude(self):
        fs = 48000
        n = 16384
        f = 1500.0  # FFT bin k=512
        t = np.arange(n * 3) / fs
        tone = 0.25 * np.sin(2*np.pi*f*t)
        result = compute_spectrum(tone, fs, SpectrumOptions(fft_size=n))
        self.assertAlmostEqual(result["peak_frequency_hz"], f, places=5)
        self.assertAlmostEqual(result["peak_amplitude_dbfs"], 20*np.log10(.25), delta=.03)

    def test_window_and_overlap(self):
        fs, n = 48000, 8192
        tone = .4*np.sin(2*np.pi*fs*128/n*np.arange(n*4)/fs)
        for window in ("boxcar", "hann", "blackman", "kaiser"):
            result = compute_spectrum(tone, fs, SpectrumOptions(
                fft_size=n, window=window, overlap_percent=50, zero_padding=2))
            self.assertGreater(result["window_count"], 1)
            self.assertAlmostEqual(result["peak_amplitude_dbfs"], 20*np.log10(.4), delta=.05)

    def test_bad_length_and_options(self):
        with self.assertRaises(ValueError):
            compute_spectrum(np.ones(100), 48000, SpectrumOptions())
        with self.assertRaises(ValueError):
            SpectrumOptions(fft_size=5000).validate()
        with self.assertRaises(ValueError):
            SpectrumOptions(fft_size=8_388_608, zero_padding=4).validate()

    def test_mel_roundtrip(self):
        f = np.array([0, 100, 1000, 20000])
        np.testing.assert_allclose(mel_to_hz(hz_to_mel(f)), f, atol=1e-8)

    def test_wav_stereo_and_channel(self):
        fs, n = 48000, 8192
        t = np.arange(n * 2)/fs
        stereo = np.column_stack((.4*np.sin(2*np.pi*fs*128/n*t),
                                  .1*np.sin(2*np.pi*fs*256/n*t)))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"test.wav"
            sf.write(str(path), stereo, fs, subtype="PCM_24")
            result = spectrum_wav(path, SpectrumOptions(fft_size=n), channel=1)
            self.assertEqual(result["channel"], 1)
            self.assertAlmostEqual(result["peak_frequency_hz"], 1500.0)
            self.assertAlmostEqual(result["peak_amplitude_dbfs"], -20.0, delta=.05)

    def test_html_txt_escaping_and_8_slots(self):
        data = {
            "fs": 48000, "level_dbfs": -18,
            "left": {
                "response": {"frequency_hz": [100,1000,10000], "response_db": [-.1,0,.1],
                             "min_db": -.1,"max_db": .1,"latency_ms": 2},
                "noise": {"noise_dbfs_rms": -98,"peak_dbfs": -79},
                "tone": {"thd_pct": .01,"thdn_pct": .04,"thdn_db": -68},
            }
        }
        data["right"] = data["left"]
        name = "<sample & eval>"
        markup = report_html([(name, data)])
        self.assertIn("&lt;sample &amp; eval&gt;", markup)
        self.assertIn("data:image/png;base64", markup)
        self.assertIn("Не сертифицировано", markup)
        txt = report_txt([(name, data)])
        self.assertIn("THD+N", txt)
        with self.assertRaises(ValueError):
            report_html([(name, data)]*9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
