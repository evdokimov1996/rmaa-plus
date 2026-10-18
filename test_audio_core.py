"""Offline checks; no sound is played by these tests."""
import unittest
import numpy as np
from audio_core import (
    analyze_frequency_response, analyze_noise, analyze_tone,
    generate_sweep, generate_tone, rms,
)


class CoreTest(unittest.TestCase):
    def test_rms(self):
        self.assertAlmostEqual(rms(np.ones(10)), 1.0)

    def test_tone_with_harmonics(self):
        fs = 48000
        t = np.arange(fs) / fs
        x = 0.2 * np.sin(2 * np.pi * 997 * t) + 0.0002 * np.sin(2 * np.pi * 1994 * t)
        report = analyze_tone(x, fs)
        self.assertAlmostEqual(report["fundamental_dbfs"], 20 * np.log10(0.2), delta=0.01)
        self.assertAlmostEqual(report["thd_pct"], 0.1, delta=0.015)

    def test_noise_level(self):
        fs = 48000
        t = np.arange(fs) / fs
        x = 0.001 * np.sin(2 * np.pi * 2000 * t)
        result = analyze_noise(x)
        self.assertAlmostEqual(result["noise_dbfs_rms"], -63.0103, delta=0.01)

    def test_sweep_flat_frequency_response(self):
        fs = 48000
        ref = generate_sweep(fs, seconds=1.0)[:, 0]
        result = analyze_frequency_response(ref, np.concatenate([np.zeros(48), ref]), fs)
        self.assertLess(abs(result["min_db"]), 0.15)
        self.assertLess(abs(result["max_db"]), 0.15)
        self.assertAlmostEqual(result["latency_ms"], 1.0, delta=0.3)

    def test_safe_tone(self):
        out = generate_tone()
        self.assertEqual(out.shape[1], 2)
        self.assertLessEqual(float(np.max(np.abs(out))), 0.13)


if __name__ == "__main__":
    unittest.main(verbosity=2)
