"""Deterministic regression for acoustic tests without microphone/audio hardware."""
import csv
import tempfile
import unittest
from pathlib import Path
import numpy as np
from scipy import signal
from rmaa_acoustic import (
    AcousticConfig, exponential_sweep, stereo_exponential_sweep,
    mic_calibration_from_csv, corrected_curve, sweep_transfer,
    thd_frequency_curve, thd_level_curve, stepped_tone_sequence,
    stepped_tone_analyze
)
from rmaa_dsp import tone


class AcousticTests(unittest.TestCase):
    FS=48000

    def test_sweep_peak_bounds_stereo(self):
        c=AcousticConfig(sample_rate=self.FS,sweep_seconds=1.2)
        stereo=stereo_exponential_sweep(c)
        self.assertEqual(stereo.ndim,2)
        self.assertEqual(stereo.shape[1],2)
        self.assertAlmostEqual(float(np.max(abs(stereo))),1.,delta=1e-6)

    def test_sweep_identity(self):
        c=AcousticConfig(sample_rate=self.FS,sweep_seconds=1.25,tail_seconds=.3)
        x=exponential_sweep(c)
        resp=sweep_transfer(x,x,self.FS,lower_hz=60,upper_hz=11000)
        self.assertAlmostEqual(resp["latency_ms"],0,delta=.1)
        self.assertLess(float(np.max(abs(np.array(resp["response_db"])))),.05)

    def test_sweep_latency(self):
        c=AcousticConfig(sample_rate=self.FS,sweep_seconds=1.2,tail_seconds=.3)
        x=exponential_sweep(c)
        y=np.concatenate((np.zeros(240),x,np.zeros(240)))
        resp=sweep_transfer(x,y,self.FS,lower_hz=80,upper_hz=11000)
        self.assertAlmostEqual(resp["latency_ms"],5,delta=.1)

    def test_sweep_highpass(self):
        c=AcousticConfig(sample_rate=self.FS,sweep_seconds=1.4,tail_seconds=.3)
        x=exponential_sweep(c)
        sos=signal.butter(2,300,fs=self.FS,btype="highpass",output="sos")
        y=signal.sosfilt(sos,x)
        resp=sweep_transfer(x,y,self.FS,lower_hz=50,upper_hz=12000)
        freq=np.array(resp["frequency_hz"])
        curve=np.array(resp["response_db"])
        self.assertLess(float(np.interp(80,freq,curve)),-10)
        self.assertAlmostEqual(float(np.interp(2000,freq,curve)),0,delta=1.0)

    def test_microphone_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"mic.csv"
            p.write_text("frequency_hz,correction_db\n20,5\n1000,0\n20000,-2\n",encoding="utf-8")
            cal=mic_calibration_from_csv(p)
            f=np.array([20,1000,20000])
            out=corrected_curve(f,np.zeros(3),cal)
            np.testing.assert_allclose(out,[5,0,-2],atol=.00001)

    def test_reject_invalid_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"bad.csv"
            p.write_text("1000,0\n20,4\n",encoding="utf-8")
            with self.assertRaises(ValueError):
                mic_calibration_from_csv(p)

    def test_thd_frequency_known(self):
        f=[600,1000,2000]
        sounds=[]
        for hz in f:
            t=np.arange(self.FS)/self.FS
            sounds.append(.1*np.sin(2*np.pi*hz*t)+.0001*np.sin(2*np.pi*2*hz*t))
        rows=thd_frequency_curve(sounds,self.FS,f)
        self.assertEqual(len(rows),3)
        for v in rows:self.assertAlmostEqual(v["thd_pct"],.1,delta=.025)

    def test_level_curve(self):
        recordings=[tone(self.FS,f=1000,peak_dbfs=n,seconds=.8) for n in (-30,-20,-10)]
        rows=thd_level_curve(recordings,self.FS,(-30,-20,-10),frequency_hz=1000)
        self.assertEqual(len(rows),3)
        self.assertLess(max(v["thd_pct"] for v in rows),.2)

    def test_stepped_generator_import(self):
        frequencies=[100,1000,8000]
        x,manifest=stepped_tone_sequence(self.FS,frequencies=frequencies,
                                          level_dbfs=-24,stage_seconds=.8)
        rows=stepped_tone_analyze(x,self.FS,manifest)
        self.assertEqual(len(rows),3)
        self.assertLess(max(v["thd_pct"] for v in rows),.2)

    def test_reject_unsafe_sweep(self):
        with self.assertRaises(ValueError):
            exponential_sweep(AcousticConfig(level_dbfs=.1))


if __name__=="__main__":
    unittest.main()
