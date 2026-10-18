"""Actual full-scale stimuli, PCM endpoints and old preference migration."""
import unittest
from unittest.mock import patch
import numpy as np
from rmaa_session import create_sequence,validate_manifest,analyze_recording
from rmaa_acoustic import AcousticConfig
from rmaa_acoustic_session import create_acoustic_sequence,validate_acoustic_manifest
from rmaa_live import LiveAnalyzer,LiveMonitor
from rmaa_backend import unpack_int24

class FullScaleTests(unittest.TestCase):
    def test_session_stages_have_full_sample_peak_except_method_signals(self):
        for fs in (44100,48000,192000,384000):
            wave,meta=create_sequence(fs,lite=True);validate_manifest(meta)
            self.assertEqual(meta['level_dbfs'],0)
            self.assertTrue(np.isfinite(wave).all());self.assertLessEqual(float(np.max(abs(wave))),1.)
            for stage in meta['stages']:
                samples=wave[stage['offset_frames']:stage['offset_frames']+stage['frames']]
                peak=float(np.max(abs(samples)))
                expected=0. if stage['name']=='noise' else .001 if stage['name']=='dynamic_range' else 1.
                self.assertAlmostEqual(peak,expected,delta=2e-6,msg=(fs,stage['name']))
    def test_fullscale_manifest_analysis_accepts_realistic_input_headroom(self):
        wave,meta=create_sequence(48000,lite=True,selected_tests=['response','thd','noise','dynamic_range'])
        result=analyze_recording(wave*.5,meta)
        self.assertEqual(result['level_dbfs'],0)
        self.assertFalse(result['calibration']['clipping_warning'])
        self.assertAlmostEqual(result['calibration']['marker_gain_db'][0],-6.0206,delta=.01)
        self.assertLess(result['left']['tone']['thd_pct'],.001)
    def test_live_default_writes_fullscale_in_all_supported_pcm_formats(self):
        analyzer=LiveAnalyzer(48000)
        for label,dtype,divisor in [('8-bit PCM','uint8',128),('16-bit PCM','int16',32768),('24-bit PCM','int24',8388608),('32-bit PCM','int32',2147483648),('32-bit float','float32',1)]:
            monitor=LiveMonitor(1,2,48000,'WASAPI Exclusive',label,{'input_map':(1,1)})
            self.assertEqual(monitor._requested_level,0)
            monitor.analyzer=analyzer;wire=monitor._make_wire(0)
            if dtype=='int24':decoded=unpack_int24(wire.tobytes(),1)[:,0]
            elif dtype=='uint8':decoded=(wire.astype(float)-128)/128
            else:decoded=wire.astype(float)/divisor
            self.assertGreater(np.max(abs(decoded)),.999 if divisor>128 else .99)
            self.assertLessEqual(np.max(abs(decoded)),1.)
            np.testing.assert_allclose(decoded,analyzer.wave,atol=1/divisor if divisor>1 else 1e-7)
    def test_acoustic_sweep_and_frequency_tones_use_fullscale(self):
        cfg=AcousticConfig(sweep_seconds=1,tail_seconds=.1)
        wave,meta=create_acoustic_sequence(cfg,lite=True);validate_acoustic_manifest(meta)
        self.assertEqual(meta['level_dbfs'],0)
        self.assertLessEqual(np.max(abs(wave)),1.)
        for stage in meta['stages']:
            part=wave[stage['offset_frames']:stage['offset_frames']+stage['frames']]
            self.assertGreater(np.max(abs(part)),.999)
        levels=meta['stages'][-1]['level_manifest']
        self.assertEqual(levels[-1]['level_dbfs'],0)
        self.assertTrue(any(level['level_dbfs']<0 for level in levels))
    def test_old_saved_level_is_not_restored_by_main_or_monitor(self):
        from rmaa_classic import Lab
        from rmaa_live_window import LiveWindow
        from rmaa_acoustic_window import AcousticWindow
        with patch('rmaa_preferences.load_preferences',return_value={'level':'-36 dBFS'}):app=Lab()
        app.withdraw()
        try:
            live=LiveWindow(app);acoustic=AcousticWindow(app)
            self.assertEqual(app.level_var.get(),'0 dBFS')
            self.assertEqual(live.level.get(),'0');self.assertEqual(acoustic.level.get(),'0')
        finally:app.destroy()
if __name__=='__main__':unittest.main()
