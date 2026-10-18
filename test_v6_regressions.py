"""Failure and precision regressions for the finite v6 measurement workflow."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from threading import Event
import numpy as np
from scipy import signal
from rmaa_session import create_sequence,analyze_recording,validate_manifest,duplex_test
from rmaa_dsp import tone_metrics,a_weight_gain
from rmaa_preferences import load_preferences,save_preferences


class WorkflowTests(unittest.TestCase):
    def test_selected_noise_really_omits_other_stimuli_and_results(self):
        wave,meta=create_sequence(selected_tests=["noise"],lite=True)
        self.assertEqual([s["name"] for s in meta["stages"]],["calibration_tone","noise"])
        result=analyze_recording(wave*.5,meta)
        self.assertEqual(set(result["left"]),{"noise"})
        json.dumps(result,allow_nan=False)

    def test_final_marker_cannot_replace_initial_marker(self):
        wave,meta=create_sequence(selected_tests=["thd"],lite=True)
        end=meta["end_marker_offset_frames"]
        wave[end:end+meta["marker_frames"]]*=1.01
        result=analyze_recording(wave*.5,meta)
        self.assertEqual(result["marker_alignment_samples"],0)

    def test_corrupt_manifest_rejected_before_marker_allocation(self):
        _,meta=create_sequence(selected_tests=["noise"],lite=True)
        meta["marker_frames"]=2**40
        with self.assertRaises(ValueError):validate_manifest(meta)

    def test_capture_preserved_when_analysis_rejects_clipping(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/"capture.wav"
            def clipped(wave,*args,**kwargs):
                result=wave.copy();result[0,0]=1.;return result
            with patch("rmaa_backend.playrec_with_format",side_effect=clipped):
                with self.assertRaisesRegex(ValueError,"Clipped"):
                    duplex_test(1,2,recording_path=dest,selected_tests=["noise"])
            self.assertTrue(dest.is_file())
            self.assertTrue(dest.with_suffix(".json").is_file())

    def test_capture_collision_fails_before_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/"capture.wav";dest.write_bytes(b"existing")
            with patch("rmaa_backend.playrec_with_format") as play:
                with self.assertRaises(FileExistsError):duplex_test(1,2,recording_path=dest)
                play.assert_not_called()
            self.assertEqual(dest.read_bytes(),b"existing")

    def test_json_filename_cannot_overwrite_wav_with_manifest(self):
        with self.assertRaises(ValueError):duplex_test(1,2,recording_path="capture.json")

    def test_mono_has_no_fabricated_right_or_channel_isolation(self):
        with patch("rmaa_backend.playrec_with_format",side_effect=lambda wave,*a,**kw:wave*.5),patch('rmaa_backend.api_for_device',return_value='Mock API'):
            result=duplex_test(1,2,input_map=(1,1),selected_tests=["thd","crosstalk"])
        self.assertEqual(result["channel_mode"],"mono")
        self.assertEqual(result["right"],{})
        self.assertNotIn("crosstalk_L_to_R",result)
        self.assertNotIn("stereo_balance",result)

    def test_constant_clock_error_corrected(self):
        wave,meta=create_sequence(selected_tests=["thd"],lite=True)
        # Known +100 ppm recorder clock. Both waveform and boundaries move.
        measured=signal.resample_poly(wave,10001,10000,axis=0,window=("kaiser",10))
        result=analyze_recording(measured*.5,meta)
        self.assertAlmostEqual(result["calibration"]["clock_drift_ppm"],100,delta=2)
        self.assertTrue(result["calibration"]["clock_correction_applied"])
        self.assertLess(result["left"]["tone"]["thdn_pct"],.002)

    def test_filter_boundary_does_not_masquerade_as_distortion(self):
        fs=48000;t=np.arange(round(1.5*fs))/fs
        wave=.2*np.sin(2*np.pi*1000.006*t+1.2)+.00002
        metrics=tone_metrics(wave,fs,f=1000)
        self.assertLess(metrics["thdn_pct"],.001)
        self.assertAlmostEqual(metrics["measured_frequency_hz"],1000.006,delta=.0001)

    def test_iec_a_weighting_high_frequency_not_bilinear_warped(self):
        values=20*np.log10(a_weight_gain([100,1000,10000,20000]))
        np.testing.assert_allclose(values,[-19.14,0,-2.49,-9.35],atol=.04)

    def test_preferences_atomic_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"preferences.json"
            save_preferences({"stream":{"input_map":[1,1]}},path)
            self.assertEqual(load_preferences(path)["stream"]["input_map"],[1,1])
            path.write_text("corrupt",encoding="utf8")
            self.assertEqual(load_preferences(path),{})


if __name__=="__main__":unittest.main()
