"""Live preview math, bounded storage and session ownership; no hardware audio."""
import unittest
from unittest.mock import patch
import numpy as np
from scipy import signal
from rmaa_live import LiveAnalyzer,LiveMonitor
from rmaa_transport import session_lease,ensure_session_available


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.analyzer=LiveAnalyzer(48000)

    def test_delay_and_gain_do_not_create_false_response(self):
        wave=np.roll(self.analyzer.wave,7331)*.02
        result=self.analyzer.response(wave[:,None])
        self.assertTrue(result['signal_found'][0])
        self.assertLess(np.nanmax(abs(result['response_db'])),.001)

    def test_filter_curve_matches_known_transfer(self):
        a=self.analyzer;sos=signal.butter(2,4000,fs=48000,output='sos')
        wave=signal.sosfilt(sos,np.tile(a.wave,3))[-48000:]*.1
        result=a.response(wave[:,None]);f=result['frequency_hz']
        _,h=signal.sosfreqz(sos,worN=f,fs=48000)
        expected=20*np.log10(abs(h));expected-=expected[f==1000]
        self.assertTrue(result['signal_found'][0])
        self.assertLess(np.nanmax(abs(result['response_db'][:,0]-expected)),.025)

    def test_silence_and_unrelated_noise_have_no_fabricated_fr(self):
        for wave in (np.zeros((48000,1)),np.random.default_rng(42).normal(0,.001,(48000,1))):
            result=self.analyzer.response(wave)
            self.assertFalse(result['signal_found'][0])
            self.assertTrue(np.isnan(result['response_db']).all())

    def test_ring_stays_bounded_and_retains_latest_samples(self):
        monitor=LiveMonitor(1,2,48000,'MME','32-bit float',{'input_map':(1,1)})
        for i in range(6):monitor._push(np.full((40000,1),i,np.float32))
        snapshot=monitor._snapshot(48000)
        self.assertEqual(monitor._ring.shape,(96000,1))
        self.assertEqual(snapshot.shape,(48000,1))
        np.testing.assert_equal(snapshot[-40000:],5)
        np.testing.assert_equal(snapshot[:8000],4)

    def test_startup_error_releases_lease_and_reports_failure(self):
        monitor=LiveMonitor(1,2,48000,'MME','32-bit PCM')
        with patch('rmaa_backend.extras_for_mode',return_value=None),patch('rmaa_live.sd.query_devices',return_value={'max_input_channels':2}),patch('rmaa_live.sd.check_input_settings',side_effect=RuntimeError('unsupported format')):
            monitor.start();monitor.thread.join(3)
        self.assertTrue(monitor.done.is_set())
        self.assertIn('unsupported format',monitor.error)
        ensure_session_available()

    def test_live_and_measurement_cannot_overlap(self):
        with session_lease():
            with self.assertRaises(RuntimeError):ensure_session_available()
            with self.assertRaises(RuntimeError):
                with session_lease():pass
        ensure_session_available()

    def test_level_outside_safe_range_rejected(self):
        monitor=LiveMonitor(1,2,48000,'MME','32-bit float')
        for value in (.1,float('nan'),-90):
            with self.assertRaises(ValueError):monitor.set_level(value)


if __name__=='__main__':unittest.main()
