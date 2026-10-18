"""Deterministic offline DSP regression; does not require audio connections."""
import unittest
import math
import numpy as np
from scipy import signal

from rmaa_dsp import (a_weight_sos, bandlimit, noise_stats, sine_fit, tone_metrics,
    dynamic_range, tone, multitone, multitone_response, imd_signal, imd_metrics,
    crosstalk, stereo_balance, delay_impulse)

class TestMeasurementDSP(unittest.TestCase):
    FS=48000
    def time(self,duration=1):
        return np.arange(int(self.FS*duration))/self.FS

    def test_a_weighting_1khz_normalized(self):
        sos=a_weight_sos(self.FS)
        _, z=signal.sosfreqz(sos,worN=[1000],fs=self.FS)
        self.assertAlmostEqual(20*np.log10(abs(z[0])),0,delta=.02)

    def test_a_weighting_low_rejection(self):
        sos=a_weight_sos(self.FS)
        _, z=signal.sosfreqz(sos,worN=[100,1000],fs=self.FS)
        self.assertLess(20*np.log10(abs(z[0])/abs(z[1])),-15)

    def test_noise_unweighted_and_a(self):
        t=self.time(1.0)
        x=.001*np.sin(2*np.pi*100*t)
        r=noise_stats(x,self.FS)
        self.assertAlmostEqual(r["noise_dbfs_rms"],-63.0103,delta=.4)
        self.assertLess(r["noise_a_dbfs_rms"],r["noise_dbfs_rms"]-13)

    def test_bandpass_rejects_low_dc_and_high(self):
        t=self.time()
        x=.02*np.ones(len(t))+.001*np.sin(2*np.pi*997*t)+.001*np.sin(2*np.pi*23000*t)
        out=bandlimit(x,self.FS)
        self.assertLess(abs(np.mean(out)),1e-5)

    def test_harmonics_0_1percent(self):
        t=self.time(1)
        x=.2*np.sin(2*np.pi*997*t)+.0002*np.sin(2*np.pi*1994*t)
        r=tone_metrics(x,self.FS)
        self.assertAlmostEqual(r["thd_pct"],.1,delta=.02)
        self.assertAlmostEqual(r["thdn_pct"],.1,delta=.025)

    def test_thdn_noise_not_thd(self):
        t=self.time(1)
        rng=np.random.default_rng(100)
        x=.2*np.sin(2*np.pi*997*t)+1e-4*rng.normal(size=len(t))
        r=tone_metrics(x,self.FS)
        self.assertLess(r["thd_pct"],.05)
        self.assertGreater(r["thdn_pct"],.03)

    def test_dynamic_range_ideal_90db(self):
        t=self.time(1)
        rng=np.random.default_rng(111)
        pilot=.001*np.sin(2*np.pi*997*t)
        noise=1e-5*rng.normal(size=len(t))
        r=dynamic_range(pilot+noise,self.FS)
        self.assertGreater(r["dynamic_range_db"],99)
        self.assertLess(r["dynamic_range_db"],103)

    def test_multitone_peak_safe_and_equal_response(self):
        ref,f=multitone(self.FS,seconds=1,n_tones=38)
        self.assertLess(np.max(abs(ref)),0.127)
        r=multitone_response(ref,ref,self.FS,f)
        self.assertLess(abs(r["min_db"]),1e-9)
        self.assertLess(abs(r["max_db"]),1e-9)

    def test_multitone_bass_rolloff(self):
        ref,f=multitone(self.FS,seconds=1,n_tones=32)
        b,a=signal.butter(2,300,fs=self.FS,btype="highpass")
        measured=signal.filtfilt(b,a,ref).astype(float)
        result=multitone_response(ref,measured,self.FS,f)
        self.assertLess(result["min_db"],-10)
        self.assertLess(abs(np.interp(1000,result["frequency_hz"],result["response_db"])),.01)

    def test_multitone_mismatch_rejected(self):
        ref,f=multitone()
        with self.assertRaises(ValueError):
            multitone_response(ref,ref[:1000],self.FS,f)

    def test_imd_smpte_synth_1percent_am(self):
        t=self.time(1.0)
        upper=.06*(1+.01*np.sin(2*np.pi*60*t))*np.sin(2*np.pi*7000*t)
        x=.24*np.sin(2*np.pi*60*t)+upper
        r=imd_metrics(x,self.FS,mode="smpte")
        self.assertAlmostEqual(r["imd_pct"],math.sqrt(2)*.01/2*100,delta=.1)

    def test_imd_smpte_clean(self):
        signal_in=imd_signal(self.FS,"smpte")
        r=imd_metrics(signal_in,self.FS,"smpte")
        self.assertLess(r["imd_pct"],.08)

    def test_imd_ccif_clean(self):
        signal_in=imd_signal(self.FS,"ccif")
        r=imd_metrics(signal_in,self.FS,"ccif")
        self.assertLess(r["imd_pct"],.15)

    def test_imd_ccif_third_order(self):
        t=self.time()
        x=.1*np.sin(2*np.pi*19000*t)+.1*np.sin(2*np.pi*20000*t)
        x+=.001*np.sin(2*np.pi*18000*t)+.001*np.sin(2*np.pi*21000*t)
        r=imd_metrics(x,self.FS,"ccif")
        self.assertGreater(r["imd_pct"],.5)

    def test_crosstalk_minus_72db(self):
        t=self.time()
        s=.1*np.sin(2*np.pi*997*t)
        r=crosstalk(s,s*10**(-72/20),self.FS)
        self.assertAlmostEqual(r["crosstalk_db"],-72,delta=.15)

    def test_stereo_balance_right_minus_6(self):
        s=.1*np.sin(2*np.pi*997*self.time())
        r=stereo_balance(s,s*.5,self.FS)
        self.assertAlmostEqual(r["right_minus_left_db"],20*np.log10(.5),delta=.1)

    def test_delay_phase(self):
        rng=np.random.default_rng(75)
        a=rng.normal(scale=.03,size=self.FS)
        delay=73
        b=np.r_[np.zeros(delay),a]
        r=delay_impulse(a,b,self.FS)
        self.assertEqual(r["latency_samples"],73)
        self.assertAlmostEqual(r["latency_ms"],73*1000/self.FS,places=3)
        self.assertGreater(np.nanmean(r["coherence"][20:200]),.95)

    def test_a_weighted_thdn_and_dynamic_range(self):
        t=self.time()
        rng=np.random.default_rng(201)
        x=.2*np.sin(2*np.pi*997*t)+.0002*rng.normal(size=len(t))
        th=tone_metrics(x,self.FS)
        self.assertIsInstance(th["thdn_a_db"],float)
        self.assertGreater(th["thdn_a_db"],-200)
        y=.001*np.sin(2*np.pi*997*t)+1e-5*rng.normal(size=len(t))
        dynamic=dynamic_range(y,self.FS)
        self.assertGreater(dynamic["dynamic_range_a_db"],50)

    def test_swept_imd_at_several_frequencies(self):
        for high in (2000.,4000.,10000.):
            x=imd_signal(self.FS,mode="smpte",seconds=.9,high_hz=high)
            res=imd_metrics(x,self.FS,mode="smpte",high_hz=high)
            self.assertLess(res["imd_pct"],.3)
            self.assertAlmostEqual(res["carrier_hz"][1],high)

    def test_safety_level(self):
        for unsafe in (.1,float("nan"),float("inf"),-121):
            with self.assertRaises(ValueError):
                tone(self.FS,peak_dbfs=unsafe)

    def test_imd_noise_a_weighting_measures_residual_instead_of_zero(self):
        from rmaa_dsp import a_weight_gain
        t=np.arange(round(1.2*self.FS))/self.FS
        x=imd_signal(self.FS,seconds=1.2)+.0001*np.sin(2*np.pi*200*t)
        result=imd_metrics(x,self.FS)
        self.assertGreater(result['imd_noise_a_pct'],0)
        measured=20*np.log10(result['imd_noise_a_pct']/result['imd_noise_pct'])
        self.assertAlmostEqual(measured,20*np.log10(a_weight_gain(200)),delta=.3)

    def test_high_order_harmonic_in_audio_band_is_not_omitted(self):
        t=self.time()
        x=.2*np.sin(2*np.pi*997*t)+.0002*np.sin(2*np.pi*15*997*t)
        result=tone_metrics(x,self.FS)
        self.assertAlmostEqual(result['thd_pct'],.1,delta=.002)
        self.assertIn(15*result['measured_frequency_hz'],result['harmonic_frequencies_hz'])

    def test_no_in_band_harmonics_is_unavailable_not_zero_thd(self):
        t=self.time()
        result=tone_metrics(.2*np.sin(2*np.pi*15000*t),self.FS,f=15000)
        self.assertIsNone(result['thd_pct'])

if __name__=="__main__":unittest.main(verbosity=2)
