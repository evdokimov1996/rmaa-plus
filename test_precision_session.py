"""Precision session acceptance and analytical noise/sideband references."""
import unittest
from unittest.mock import patch
import numpy as np
from rmaa_dsp import tone_metrics, bandlimit, rms
from rmaa_session import create_sequence, validate_manifest, analyze_recording


class PrecisionSessionTests(unittest.TestCase):
    def test_long_tone_and_spectrum_resolve_known_noise(self):
        fs=48000
        t=np.arange(fs*6)/fs
        noise=np.random.default_rng(821).normal(0,1e-5,len(t))
        wave=.5*np.sin(2*np.pi*1000.031*t)+noise
        result=tone_metrics(wave,fs,f=1000.)
        self.assertAlmostEqual(result['thdn_db'],20*np.log10(rms(bandlimit(noise,fs))/(.5/np.sqrt(2))),delta=.04)

    def test_shared_global_fit_keeps_am_sidebands(self):
        fs=48000;t=np.arange(fs*6)/fs
        wave=.5*(1+.001*np.sin(2*np.pi*4*t))*np.sin(2*np.pi*1000*t)
        result=tone_metrics(wave,fs,f=1000.)
        # The settling trims leave a noninteger number of 4 Hz AM cycles.
        self.assertAlmostEqual(result['thdn_db'],20*np.log10(.001/np.sqrt(2)),delta=.05)

    def test_full_precision_manifest_is_finite_and_keeps_peak_level(self):
        wave,meta=create_sequence(profile='precision',selected_tests=['response','noise','thd','imd_sweep'])
        validate_manifest(meta)
        self.assertEqual(meta['measurement_profile'],'precision')
        self.assertEqual(meta['level_dbfs'],0)
        self.assertAlmostEqual(float(np.max(abs(wave))),1.,places=6)
        stages={p['name']:p for p in meta['stages']}
        self.assertEqual(stages['thd']['frames'],48000*6)
        self.assertEqual(len(stages['imd_sweep']['metadata']['steps']),64)
        self.assertGreater(stages['frequency_response']['frames'],48000*8)

    def test_budget_rejected_before_generator_allocation(self):
        with patch('rmaa_session.np.zeros',side_effect=AssertionError('allocated before preflight')):
            with self.assertRaisesRegex(ValueError,'лимит памяти'):
                create_sequence(fs=384000,profile='precision')

    def test_dense_identity_session_and_mono(self):
        wave,meta=create_sequence(level_dbfs=-6,profile='precision',selected_tests=['response','noise','thd'])
        meta['input_map']=[1,1]
        result=analyze_recording(wave,meta)
        self.assertEqual(result['right'],{})
        response=result['left']['response']
        self.assertGreater(len(response['frequency_hz']),19000)
        self.assertLess(max(abs(np.array(response['response_db']))),.001)
        self.assertEqual(result['measurement_profile'],'precision')
        self.assertLess(result['left']['tone']['spectrum']['frequency_hz'][1],.5)

    def test_independent_fourier_clock_stretch_and_fractional_delay(self):
        from scipy.signal import resample
        wave,meta=create_sequence(level_dbfs=-6,profile='precision',selected_tests=['response'])
        meta['input_map']=[1,1]
        # An independent Fourier interpolation creates a physically stretched
        # capture, rather than reusing the production sinc as the reference.
        stretched=resample(wave,len(wave)+23,axis=0)
        captured=np.vstack((np.zeros((137,2)),stretched,np.zeros((128,2))))
        result=analyze_recording(captured,meta)
        response=result['left']['response']
        self.assertTrue(response['valid'],response.get('reason'))
        self.assertLess(max(abs(np.array(response['response_db']))),.005)
