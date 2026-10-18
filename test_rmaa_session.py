"""Self-contained offline workflow testing. No audio device invoked."""
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf
from rmaa_session import create_sequence, find_recording_offset, analyze_recording, save_offline, import_offline

class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wave,cls.manifest=create_sequence(fs=48000,level_dbfs=-18,lite=True)
    def test_marker_offset(self):
        delay=129
        data=np.concatenate((np.zeros((delay,2)),self.wave),axis=0)
        found,similarity=find_recording_offset(data,self.manifest)
        self.assertEqual(found,delay)
        self.assertGreater(similarity,.99)
    def test_known_ideal_loopback(self):
        result=analyze_recording(self.wave,self.manifest,"synthetic perfect loopback")
        self.assertGreater(result["marker_coherence"],.99)
        self.assertEqual(result["fs"],48000)
        self.assertLess(abs(result["left"]["response"]["min_db"]),.01)
        self.assertLess(abs(result["right"]["response"]["max_db"]),.01)
        self.assertLess(result["left"]["tone"]["thd_pct"],.1)
        self.assertLess(result["left"]["imd_smpte"]["imd_pct"],.15)
        self.assertLess(result["left"]["imd_ccif"]["imd_pct"],.2)
        self.assertLess(abs(result["stereo_balance"]["right_minus_left_db"]),.05)
        self.assertLess(result["crosstalk_L_to_R"]["crosstalk_db"],-90)
        self.assertGreater(np.mean(result["left"]["phase"]["coherence"][5:100]),.9)
    def test_delayed_audio(self):
        delay=240
        input=np.vstack((np.zeros((delay,2)),self.wave, np.zeros((delay,2))))
        result=analyze_recording(input,self.manifest)
        self.assertEqual(result["marker_alignment_samples"],delay)
    def test_offline_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            wav,manifest=save_offline(folder,fs=48000,level_dbfs=-18)
            self.assertTrue(wav.is_file())
            self.assertTrue(manifest.is_file())
            output=import_offline(wav,manifest)
            self.assertLess(abs(output["left"]["response"]["min_db"]),.08)
            self.assertEqual(output["fs"],48000)
    def test_corrupted_marker(self):
        altered=self.wave.copy()
        start=self.manifest["marker_offset_frames"]
        altered[start:start+self.manifest["marker_frames"]]=0
        with self.assertRaises(ValueError):
            analyze_recording(altered,self.manifest)
    def test_clipping_fails(self):
        altered=self.wave.copy()
        altered[4,0]=1.0
        with self.assertRaises(ValueError):
            analyze_recording(altered,self.manifest)
    def test_sample_rate_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            wav,manifest=save_offline(folder,fs=48000,level_dbfs=-18)
            with sf.SoundFile(str(wav)) as a:
                data=a.read(dtype="float32")
            sf.write(str(Path(folder)/"bad.wav"),data,44100,subtype="PCM_24")
            with self.assertRaises(ValueError):
                import_offline(Path(folder)/"bad.wav",manifest)

    def test_mono_wav_import_does_not_create_second_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            wave,meta=create_sequence(selected_tests=['thd'],lite=True)
            audio=Path(folder)/'mono.wav';manifest=Path(folder)/'mono.json'
            sf.write(audio,wave[:,0]*.5,48000,subtype='FLOAT')
            manifest.write_text(json.dumps(meta),encoding='utf8')
            result=import_offline(audio,manifest)
            self.assertEqual(result['right'],{})
            self.assertEqual(result['channel_mode'],'mono')
            self.assertNotIn('stereo_balance',result)
    def test_inverted_right_polarity(self):
        altered=self.wave.copy()
        altered[:,1]*=-1
        result=analyze_recording(altered,self.manifest)
        self.assertGreater(result["marker_coherence"],.99)
        self.assertEqual(result["calibration"]["phase_polarity"][1],"inverted")
        self.assertEqual(result["calibration"]["phase_polarity"][0],"normal")
        self.assertLess(abs(result["calibration"]["marker_gain_db"][0]),.05)
        self.assertLess(abs(result["calibration"]["marker_gain_db"][1]),.05)

    def test_high_sample_rate_stimulus(self):
        wave,manifest=create_sequence(fs=384000,level_dbfs=-24,lite=True)
        self.assertEqual(manifest["fs"],384000)
        self.assertEqual(wave.shape[1],2)
        self.assertLess(np.max(abs(wave)),.07)

    def test_live_simulated_recording_saved_as_wav(self):
        from unittest.mock import patch
        from rmaa_session import duplex_test
        import json
        with tempfile.TemporaryDirectory() as root:
            dest=Path(root)/"capture.wav"
            def device(_):
                return {"max_output_channels":2,"max_input_channels":2}
            with patch("rmaa_session.sd.query_devices",side_effect=device):
                with patch("rmaa_backend.playrec_with_format",
                           side_effect=lambda wave,*args,**kwargs:wave):
                    report=duplex_test(1,2,fs=48000,level_dbfs=-18,
                                       recording_path=str(dest))
            self.assertTrue(dest.is_file())
            self.assertTrue(dest.with_suffix(".json").is_file())
            with sf.SoundFile(str(dest)) as file:
                self.assertEqual(file.samplerate,48000)
                self.assertEqual(file.channels,2)
                self.assertEqual(file.subtype,"FLOAT")
            metadata=json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["fs"],48000)
            self.assertEqual(report["saved_recording"],str(dest))
            self.assertEqual(report["left"]["tone"]["frequency_hz"],1000.0)

    def test_safe_peak(self):
        self.assertLessEqual(abs(self.wave).max(),.13)

if __name__=="__main__":unittest.main()
