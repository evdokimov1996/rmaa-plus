"""Deterministic callback driver. Never opens a real audio device."""
import unittest
from unittest.mock import patch
from threading import Event
import numpy as np
import sounddevice as sd
from rmaa_transport import transfer, MeasurementCancelled
from rmaa_backend import pack_int24, _float_to_integer


class StreamTests(unittest.TestCase):
    def setUp(self):
        devices=patch('rmaa_transport.sd.query_devices',return_value={'max_input_channels':8})
        devices.start();self.addCleanup(devices.stop)

    def run_stream(self,dtype="float32",input_map=(1,2),output_map=(1,2),status=None,cancel=None):
        rng=np.random.default_rng(54)
        source=rng.uniform(-.3,.3,(283,2)).astype(np.float32)
        received=[]
        class FakeStream:
            def __init__(self,**kw):
                if kw.get('dither_off') is not True:raise AssertionError('Measurement must disable PortAudio conversion dither')
                self.kw=kw
            def __enter__(self):
                inch,outch=self.kw["channels"]
                for start in range(0,len(source),64):
                    n=min(64,len(source)-start)
                    incoming=np.zeros((64,inch),np.float32)
                    incoming[:n,np.asarray(input_map)-1]=source[start:start+n]
                    if dtype=="int24":
                        incoming=memoryview(pack_int24(incoming))
                        outgoing=bytearray(64*outch*3)
                    elif dtype=="uint8":
                        incoming=memoryview(np.rint(incoming*128+128).astype(np.uint8).tobytes())
                        outgoing=bytearray(64*outch)
                    else:
                        incoming=_float_to_integer(incoming,dtype)
                        outgoing=np.empty((64,outch),incoming.dtype)
                    try:self.kw["callback"](incoming,outgoing,64,None,status)
                    except (sd.CallbackStop,sd.CallbackAbort):
                        received.append(outgoing)
                        break
                    received.append(outgoing)
                self.kw["finished_callback"]()
                return self
            def __exit__(self,*args):return False
            def abort(self):pass
        raw=dtype in ("int24","uint8")
        with patch("rmaa_transport.sd.RawStream" if raw else "rmaa_transport.sd.Stream",FakeStream):
            captured=transfer(source,48000,1,1,dtype=dtype,input_map=input_map,output_map=output_map,cancel=cancel)
        return source,captured,received

    def test_float_stereo_short_final_block(self):
        source,captured,blocks=self.run_stream()
        np.testing.assert_array_equal(source,captured)
        self.assertEqual(len(blocks),5)
        np.testing.assert_array_equal(blocks[-1][27:],0)

    def test_integer_formats(self):
        for dtype,tolerance in (("int16",1/32768),("int24",2/8388608),("int32",1e-7),("uint8",1/128)):
            with self.subTest(dtype=dtype):
                source,captured,_=self.run_stream(dtype)
                np.testing.assert_allclose(source,captured,atol=tolerance)

    def test_maps_and_silent_unused_outputs(self):
        source,captured,blocks=self.run_stream(input_map=(3,4),output_map=(2,4))
        np.testing.assert_array_equal(source,captured)
        for block in blocks:np.testing.assert_array_equal(block[:,[0,2]],0)

    def test_discontinuity_invalidates_measurement(self):
        with self.assertRaisesRegex(RuntimeError,"discontinuity"):
            self.run_stream(status="input overflow")

    def test_cancel_before_device_open(self):
        cancel=Event();cancel.set()
        with self.assertRaises(MeasurementCancelled):self.run_stream(cancel=cancel)

    def test_cancel_stalled_driver_closes(self):
        cancel=Event();closed=[]
        class Stalled:
            def __init__(self,**kw):pass
            def __enter__(self):cancel.set();return self
            def abort(self):closed.append("aborted")
            def __exit__(self,*args):closed.append("closed")
        with patch("rmaa_transport.sd.Stream",Stalled):
            with self.assertRaises(MeasurementCancelled):
                transfer(np.zeros((200,2)),48000,1,1,cancel=cancel)
        self.assertEqual(closed,["aborted","closed"])

    def test_early_finish_rejected(self):
        class Short:
            def __init__(self,**kw):self.kw=kw
            def __enter__(self):self.kw["finished_callback"]();return self
            def __exit__(self,*args):return False
        with patch("rmaa_transport.sd.Stream",Short):
            with self.assertRaisesRegex(RuntimeError,"complete recording"):
                transfer(np.zeros((200,2)),48000,1,1)

    def test_reject_duplicate_output(self):
        with self.assertRaises(ValueError):self.run_stream(output_map=(1,1))

    def test_independent_streams_keep_capture_margin_and_close_both(self):
        fs=8000;source=np.full((1000,2),.1,np.float32);closed=[];outputs=[]
        class Input:
            def __init__(self,**kw):
                if kw.get('dither_off') is not True:raise AssertionError('Input conversion dither enabled')
                self.kw=kw
            def __enter__(self):
                while True:
                    try:self.kw["callback"](np.full((128,2),.01,np.float32),128,None,None)
                    except sd.CallbackStop:break
                self.kw["finished_callback"]();return self
            def __exit__(self,*args):closed.append("input")
        class Output:
            def __init__(self,**kw):
                if kw.get('dither_off') is not True:raise AssertionError('Output conversion dither enabled')
                self.kw=kw
            def __enter__(self):
                while True:
                    out=np.empty((128,2),np.float32)
                    try:self.kw["callback"](out,128,None,None)
                    except sd.CallbackStop:outputs.append(out.copy());break
                    outputs.append(out.copy())
                self.kw["finished_callback"]();return self
            def __exit__(self,*args):closed.append("output")
        with patch("rmaa_transport.sd.InputStream",Input),patch("rmaa_transport.sd.OutputStream",Output),patch("rmaa_transport.sd.query_devices",return_value={"max_input_channels":2}):
            captured=transfer(source,fs,1,2)
        self.assertEqual(len(captured),len(source)+2*fs)
        np.testing.assert_allclose(captured,.01)
        self.assertCountEqual(closed,["input","output"])
        np.testing.assert_allclose(np.concatenate(outputs)[:len(source)],source)


if __name__=="__main__":unittest.main()
