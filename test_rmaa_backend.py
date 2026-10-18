"""Offline tests of packed 24-bit PCM and explicit audio API routing."""
import unittest
from unittest.mock import patch
import numpy as np
import sounddevice as sd

from rmaa_backend import (
    pack_int24, unpack_int24, playrec_with_format, validate_mode,
    extras_for_mode, PCM_FORMATS
)

class BackendTests(unittest.TestCase):
    def test_pcm24_endpoints(self):
        stereo=np.array([
            [-1.,-0.5],[0.,0.5],[.999999,.123456],
        ],dtype=np.float64)
        packed=pack_int24(stereo)
        self.assertEqual(len(packed),3*2*3)
        back=unpack_int24(packed)
        np.testing.assert_allclose(stereo,back,atol=2/8388608)

    def test_pcm24_reject_bad_bytes(self):
        with self.assertRaises(ValueError):
            unpack_int24(b"\0\1",2)

    def test_pcm24_random_no_16bit_truncation(self):
        rng=np.random.default_rng(32)
        wave=rng.uniform(-1,1,(200,2))
        reconstructed=unpack_int24(pack_int24(wave))
        self.assertLess(np.max(np.abs(wave-reconstructed)),2/8388608)

    def test_validate_non_wasapi_reject(self):
        with patch("rmaa_backend.api_for_device",side_effect=lambda _:"MME"):
            with self.assertRaises(ValueError):
                validate_mode(2,3,"WASAPI Exclusive")

    def test_asio_reject_no_driver(self):
        with patch("rmaa_backend.api_for_device",side_effect=lambda _:"Windows WASAPI"):
            with self.assertRaises(ValueError):
                validate_mode(3,4,"ASIO")

    def test_wasapi_settings_created(self):
        with patch("rmaa_backend.api_for_device",side_effect=lambda _:"Windows WASAPI"):
            if hasattr(sd,"WasapiSettings"):
                settings=extras_for_mode(3,4,"WASAPI Exclusive")
                self.assertEqual(len(settings),2)

    def test_int16_format_playrec(self):
        wave=np.array([[.5,-.5],[-.3,.2]],dtype=np.float32)
        with patch("rmaa_backend.api_for_device",return_value="MME"),patch("rmaa_backend.sd.check_input_settings"),patch("rmaa_backend.sd.check_output_settings"),patch("rmaa_transport.transfer",return_value=wave) as fake:
            out=playrec_with_format(wave,48000,1,2,format_label="16-bit PCM")
        self.assertEqual(fake.call_args.kwargs["dtype"],"int16")
        np.testing.assert_array_equal(out,wave)

    def test_float32_passthrough(self):
        wave=np.array([[.25,-.25],[.1,-.2]],dtype=np.float32)
        with patch("rmaa_backend.api_for_device",return_value="MME"),patch("rmaa_backend.sd.check_input_settings"),patch("rmaa_backend.sd.check_output_settings"),patch("rmaa_transport.transfer",return_value=wave):
            out=playrec_with_format(wave,48000,1,2)
        np.testing.assert_array_equal(out,wave)

    def test_int24_duplex_simulated_callback(self):
        """Exercise RawStream callback/packing path, without audio hardware."""
        rng=np.random.default_rng(11)
        source=rng.uniform(-.3,.3,(512,2)).astype(np.float32)
        from rmaa_backend import pack_int24
        wire=pack_int24(source)

        class FakeRawStream:
            def __init__(self,**kwargs):
                self.kwargs=kwargs
            def __enter__(self):
                data=memoryview(wire)
                output=bytearray(len(wire))
                try:
                    self.kwargs["callback"](data,output,len(source),None,None)
                except sd.CallbackStop:
                    pass
                self.assert_wire=bytes(output)
                self.kwargs["finished_callback"]()
                return self
            def __exit__(self,*args):
                return False

        with patch("rmaa_backend.api_for_device",side_effect=lambda _:"MME"):
            with patch("rmaa_backend.sd.check_input_settings"):
                with patch("rmaa_backend.sd.check_output_settings"):
                    with patch("rmaa_backend.sd.RawStream",side_effect=FakeRawStream):
                        from rmaa_backend import _stream_record_raw24
                        captured=_stream_record_raw24(source,48000,(1,2))
        np.testing.assert_allclose(captured,source,atol=2/8388608)

    def test_no_unsupported_dtype(self):
        with self.assertRaises(ValueError):
            playrec_with_format(np.zeros((100,2)),48000,1,2,format_label="64-bit float")


if __name__=="__main__":
    unittest.main(verbosity=2)
