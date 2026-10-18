"""Analytical reference checks independent of audio hardware and RMAA files."""
import unittest
import math
from unittest import mock
import numpy as np
from rmaa_dsp import (bandlimit,noise_stats,tone_metrics,dynamic_range,sine_fit,
                      imd_metrics,imd_signal,fit_frequency)

class TestPrecisionReference(unittest.TestCase):
    FS=48000
    def samples(self,seconds):return np.arange(round(self.FS*seconds))/self.FS

    def test_perfect_sine_floor_long_and_clock_offset(self):
        t=self.samples(6.)
        for f in (997.,1000.,1000.031,1001.25):
            with self.subTest(f=f):
                r=tone_metrics(.2*np.sin(2*np.pi*f*t),self.FS,f=997 if f==997 else 1000)
                self.assertLess(r['thdn_db'],-140)
                self.assertAlmostEqual(r['measured_frequency_hz'],f,delta=1e-9)

    def test_known_inband_tone_noise_no_band_edge_attenuation(self):
        t=self.samples(6.)
        expected=20*np.log10(1e-4/np.sqrt(2))
        for f in (20.,25.,40.,1000.,19000.,20000.):
            with self.subTest(f=f):
                value=noise_stats(1e-4*np.sin(2*np.pi*f*t),self.FS,False)
                self.assertAlmostEqual(value['noise_dbfs_rms'],expected,delta=.0001)

    def test_band_rejects_dc_and_outside_bins(self):
        t=self.samples(2.)
        x=.1+np.sin(2*np.pi*10*t)+np.sin(2*np.pi*21000*t)
        self.assertLess(np.sqrt(np.mean(bandlimit(x,self.FS)**2)),1e-10)

    def test_known_thd_and_thdn_high_band_edge(self):
        t=self.samples(6.)
        for harmonic in (2,15,19,20):
            with self.subTest(harmonic=harmonic):
                x=.2*np.sin(2*np.pi*1000*t)+.0002*np.sin(2*np.pi*harmonic*1000*t)
                r=tone_metrics(x,self.FS,f=1000)
                self.assertAlmostEqual(r['thd_pct'],.1,delta=1e-6)
                self.assertAlmostEqual(r['thdn_pct'],.1,delta=.00005)

    def test_dr_reference_conventions_preserved(self):
        t=self.samples(6.)
        x=.001*np.sin(2*np.pi*1000*t)+1e-5*np.sin(2*np.pi*100*t)
        r=dynamic_range(x,self.FS,f=1000)
        self.assertAlmostEqual(r['dynamic_range_sine_rms_db'],100.,delta=.001)
        self.assertAlmostEqual(r['dynamic_range_db'],103.0102999566,delta=.001)

    def test_dense_ccif_sweep_products_unique_inband_not_carriers(self):
        t=self.samples(1.5)
        for center in (550.,1000.,1500.,2000.,10000.,18500.,19000.):
            with self.subTest(center=center):
                r=imd_metrics(imd_signal(self.FS,'ccif_sweep',seconds=1.5,high_hz=center),self.FS,'ccif_sweep',high_hz=center)
                products=r['products_hz']; carriers=r['measured_carrier_hz']
                self.assertTrue(all(20-1e-6<=f<=20000+1e-6 for f in products))
                self.assertEqual(len(products),len(set(products)))
                self.assertTrue(all(abs(f-c)>1e-6 for f in products for c in carriers))
                self.assertLess(r['imd_db'],-135.)

    def test_ccif_analytically_known_difference_product(self):
        t=self.samples(3.)
        x=.1*np.sin(2*np.pi*19000*t)+.1*np.sin(2*np.pi*20000*t)+.001*np.sin(2*np.pi*1000*t)
        r=imd_metrics(x,self.FS,'ccif')
        expected=100*.001/math.hypot(.1,.1)
        self.assertAlmostEqual(r['imd_pct'],expected,delta=1e-5)
        self.assertAlmostEqual(r['imd_noise_pct'],expected,delta=.001)
        self.assertNotIn(21000.,r['products_hz'])

    def test_near_coincident_tones_use_stable_qr_fallback(self):
        t=self.samples(1.)
        x=.2*np.sin(2*np.pi*1000*t)+.05*np.cos(2*np.pi*1000.001*t)+.01
        amps,residual,dc=sine_fit(x,self.FS,[1000.,1000.001])
        np.testing.assert_allclose(amps,[.2,.05],atol=1e-8)
        self.assertLess(np.max(abs(residual)),1e-10)
        self.assertAlmostEqual(dc,.01,places=11)

    def test_large_capture_never_allocates_full_design_matrix(self):
        # Instrument the actual basis helper: production memory is bounded by
        # block size for high-rate captures regardless of number of harmonics.
        import rmaa_dsp as dsp
        fs=384000;n=fs*6;t=np.arange(n)/fs
        freq=np.arange(1,21)*1000.
        x=.2*np.sin(2*np.pi*1000*t)+.0002*np.cos(2*np.pi*2000*t)
        original=dsp._fit_basis; sizes=[]
        def observe(start,stop,*args):
            sizes.append(stop-start)
            return original(start,stop,*args)
        with mock.patch.object(dsp,'_fit_basis',side_effect=observe):
            amps,residual,_=sine_fit(x,fs,freq)
        self.assertLessEqual(max(sizes),16384)
        self.assertAlmostEqual(amps[0],.2,places=10)
        self.assertAlmostEqual(amps[1],.0002,places=10)
        self.assertLess(20*np.log10(np.sqrt(np.mean(residual**2))/.2),-140)

if __name__=='__main__':unittest.main(verbosity=2)
