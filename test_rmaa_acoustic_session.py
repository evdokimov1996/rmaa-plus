"""Verify acoustic offline file compatibility and synchronization."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf

from rmaa_acoustic import AcousticConfig
from rmaa_acoustic_session import (
    create_acoustic_sequence, analyze_acoustic_capture,
    save_acoustic_files, import_acoustic_files
)

class AcousticSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=AcousticConfig(sample_rate=48000,sweep_seconds=1.2,tail_seconds=.3,
                               end_hz=15000,level_dbfs=-24)
        cls.test,cls.manifest=create_acoustic_sequence(cls.cfg,lite=True)

    def test_perfect_loopback(self):
        r=analyze_acoustic_capture(self.test,self.manifest)
        self.assertGreater(r["marker_similarity"],.99)
        self.assertLess(abs(r["left"]["response"]["min_db"]),.2)
        self.assertLess(abs(r["right"]["response"]["max_db"]),.2)
        self.assertLess(max(x["thd_pct"] for x in r["left"]["thd_frequency"]),.4)
        self.assertLess(max(x["thd_pct"] for x in r["left"]["thd_level"]),.4)

    def test_delayed_recording(self):
        shifted=np.vstack((np.zeros((233,2)),self.test,np.zeros((233,2))))
        r=analyze_acoustic_capture(shifted,self.manifest)
        self.assertEqual(r["alignment_frames"],233)

    def test_inversion(self):
        changed=self.test.copy()
        changed[:,1]*=-1
        r=analyze_acoustic_capture(changed,self.manifest)
        self.assertEqual(r["calibration"]["polarity"],["normal","inverted"])

    def test_offline_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            wave,meta=save_acoustic_files(folder,self.cfg)
            self.assertTrue(wave.is_file())
            self.assertTrue(meta.is_file())
            analyzed=import_acoustic_files(wave,meta)
            self.assertEqual(analyzed["fs"],48000)
            self.assertGreater(analyzed["marker_similarity"],.99)

    def test_reject_clip(self):
        corrupted=self.test.copy()
        corrupted[100,0]=1.0
        with self.assertRaises(ValueError):
            analyze_acoustic_capture(corrupted,self.manifest)

    def test_bad_recording_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            wave,meta=save_acoustic_files(folder,self.cfg)
            changed=Path(folder)/"mismatch.wav"
            sf.write(changed,np.zeros((48000,2),dtype=np.float32),44100)
            with self.assertRaises(ValueError):
                import_acoustic_files(changed,meta)

    def test_subwoofer_never_plays_steps_outside_selected_band(self):
        cfg=AcousticConfig(start_hz=10,end_hz=300,sweep_seconds=2,detail="subwoofer")
        wave,meta=create_acoustic_sequence(cfg,lite=True)
        steps=next(s for s in meta['stages'] if s['name']=='thd_vs_frequency')
        self.assertTrue(all(10<=s['frequency_hz']<=300 for s in steps['step_manifest']))
        levels=next(s for s in meta['stages'] if s['name']=='thd_vs_level')
        self.assertEqual(levels['frequency_hz'],100)
        result=analyze_acoustic_capture(wave*.5,meta)
        response=result['left']['response']
        self.assertAlmostEqual(response['reference_hz'],150)
        self.assertEqual(response['frequency_hz'][0],10)
        self.assertLess(abs(response['min_db']),.2)

    def test_acoustic_mono_uses_selected_channel_without_fake_right(self):
        from unittest.mock import patch
        from rmaa_acoustic_session import acoustic_live
        with patch('rmaa_backend.playrec_with_format',side_effect=lambda wave,*a,**kw:wave) as stream:
            result=acoustic_live(1,2,self.cfg,stream_options={'input_map':(1,1),'output_map':(1,2)})
        self.assertEqual(stream.call_args.kwargs['input_map'],(1,1))
        self.assertEqual(result['right'],{})
        self.assertEqual(result['channel_mode'],'mono')

    def test_failed_acoustic_analysis_keeps_completed_raw_capture(self):
        from unittest.mock import patch
        from rmaa_acoustic_session import acoustic_live
        def clipped(wave,*args,**kwargs):
            wave=wave.copy();wave[0,0]=1.;return wave
        with tempfile.TemporaryDirectory() as folder:
            capture=Path(folder)/'capture.wav'
            with patch('rmaa_backend.playrec_with_format',side_effect=clipped):
                with self.assertRaises(ValueError):acoustic_live(1,2,self.cfg,recording_path=capture)
            self.assertTrue(capture.is_file())
            self.assertTrue(capture.with_suffix('.json').is_file())
            with patch('rmaa_backend.playrec_with_format') as play:
                with self.assertRaises(FileExistsError):acoustic_live(1,2,self.cfg,recording_path=capture)
                play.assert_not_called()

    def test_huge_manifest_marker_rejected_before_allocation(self):
        import copy
        meta=copy.deepcopy(self.manifest);meta['marker_frames']=2**40
        with self.assertRaises(ValueError):analyze_acoustic_capture(self.test,meta)

    def test_acoustic_mono_wav_import_has_one_result_channel(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            wave=Path(folder)/'mono.wav';meta=Path(folder)/'mono.json'
            sf.write(wave,self.test[:,0],48000,subtype='FLOAT')
            meta.write_text(json.dumps(self.manifest),encoding='utf8')
            result=import_acoustic_files(wave,meta)
            self.assertEqual(result['right'],{})
            self.assertEqual(result['channel_mode'],'mono')

if __name__=="__main__":unittest.main(verbosity=2)
