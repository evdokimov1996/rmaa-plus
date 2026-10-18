import struct
import tempfile
import unittest
from pathlib import Path
from rmaa_sav import load_sav


def fixture():
    data=bytearray(151);data[0]=0xf9
    data[1:5]=b"Test";data[51:55]=b"Mode"
    for index,delta in enumerate((40,44,32,40,24,32,32,24,24)):
        values=[0.01]*(int((delta-8)//4))
        if index==1:values[:6]=[-101.,-102.,-103.,-104.,-80.,-81.]
        body=struct.pack("<"+"f"*len(values),*values)+bytes(4)+struct.pack("<6I",48000,24,2,3,1,0)
        data+=struct.pack("<I",len(body))+body
    data+=struct.pack("<IIffI",16,0,-4.,-4.,0)
    return bytes(data)


class SavTests(unittest.TestCase):
    def read(self,data):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"result.sav";p.write_bytes(data)
            return load_sav(p)

    def test_scalar_layout(self):
        r=self.read(fixture())
        self.assertEqual(r["fs"],48000)
        self.assertEqual(r["device_name"],"Test")
        self.assertEqual(r["left"]["noise"]["noise_a_dbfs_rms"],-103)
        self.assertEqual(r["right"]["noise"]["noise_a_dbfs_rms"],-104)
        self.assertEqual(r["calibration"]["level_change_db"],[-4,-4])
        self.assertAlmostEqual(r["left"]["tone"]["thdn_db"],-80,places=4)
        self.assertEqual(r["source_format"],"RMAA F9 SAV")
        self.assertNotIn("frequency_hz",r["left"]["response"])

    def test_unknown_version_rejected(self):
        data=bytearray(fixture());data[0]=0xf8
        with self.assertRaises(ValueError):self.read(data)

    def test_truncated_and_extended_rejected(self):
        for data in (fixture()[:-3],fixture()+b"extra"):
            with self.assertRaises(ValueError):self.read(data)

    def test_bad_extent_rejected(self):
        data=bytearray(fixture());struct.pack_into("<I",data,151,0xffffffff)
        with self.assertRaises(ValueError):self.read(data)

    def test_full_graph_codec_and_missing_records_roundtrip(self):
        from rmaa_sav import save_sav,load_sav,_quantize
        import numpy as np,base64
        data={"fs":48000,"left":{"response":{"frequency_hz":[20.,1000.,20000.],"response_db":[-.12,0.,.02],"min_20_20000_db":-.12,"max_20_20000_db":.02,"min_db":-.03,"max_db":.01}},"right":{},"calibration":{"calibration_tone_gain_db":-4.2}}
        with tempfile.TemporaryDirectory() as folder:
            a=Path(folder)/"a.sav";b=Path(folder)/"b.sav"
            save_sav(a,data)
            first=load_sav(a)
            self.assertEqual(sum(g is not None for g in first["sav_graphs"]),1)
            np.testing.assert_allclose(first["left"]["response"]["response_db"],[-.12,0.,.02],atol=.0024)
            save_sav(b,first)
            second=load_sav(b)
            self.assertEqual(first["sav_graphs"],second["sav_graphs"])
            self.assertEqual(first["sav_statistics"],second["sav_statistics"])
            with self.assertRaises(FileExistsError):save_sav(b,first)

    def test_invalid_graph_extent_rejected(self):
        data=bytearray(fixture())
        # The fixture has scalar-only zero-length graphs. Claiming points without
        # their encoded payload must fail before a reshape/allocation.
        struct.pack_into("<I",data,151+40+20,1000000)
        with self.assertRaises(ValueError):self.read(data)

    def test_negative_percentage_and_nonfinite_footer_rejected(self):
        data=bytearray(fixture());cursor=151
        for _ in range(3):cursor+=4+struct.unpack_from("<I",data,cursor)[0]
        struct.pack_into("<f",data,cursor+4,-1.)
        with self.assertRaises(ValueError):self.read(data)
        data=bytearray(fixture());struct.pack_into("<f",data,len(data)-12,float("nan"))
        with self.assertRaises(ValueError):self.read(data)

    def test_all_nine_graph_records_and_single_channel_roundtrip(self):
        from rmaa_sav import DELTAS,FFT_RECORDS,save_sav
        import numpy as np
        data=bytearray(fixture()[:151])
        for record,delta in enumerate(DELTAS):
            n=131072 if record in FFT_RECORDS else 3
            if record in FFT_RECORDS:
                bins=np.zeros(n//2+1,dtype='<u2');bins[12345]=64000
                payload=bins.tobytes()
            else:payload=np.array([20,1000,20000,43333,43400,43333],dtype='<u2').tobytes()
            values=[.001]*((delta-8)//4)
            body=struct.pack('<'+'f'*len(values),*values)+bytes(4)+struct.pack('<6I',48000,24,1,3,1,n)+payload
            data+=struct.pack('<I',len(body))+body
        data+=struct.pack('<IIfff',16,0,-4.,-4.,0.)
        first=self.read(data)
        self.assertEqual(len(first['sav_graphs']),9)
        spectrum=first['left']['tone']['spectrum']
        self.assertLessEqual(len(spectrum['frequency_hz']),32768)
        self.assertIn(12345*48000/131072,spectrum['frequency_hz'])
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'all.sav';save_sav(dest,first)
            second=load_sav(dest)
            self.assertEqual(first['sav_graphs'],second['sav_graphs'])
            self.assertEqual(first['sav_statistics'],second['sav_statistics'])
            first['sav_graphs'][3]['n']=131073
            invalid=Path(folder)/'invalid.sav'
            with self.assertRaises(ValueError):save_sav(invalid,first)
            self.assertFalse(invalid.exists())


if __name__=="__main__":unittest.main()
