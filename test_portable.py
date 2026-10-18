"""Portable settings follow the EXE, never cwd or PyInstaller _internal."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from rmaa_preferences import preference_path,load_preferences,save_preferences

class PortableTests(unittest.TestCase):
    def test_profile_mode_preserves_existing_location_without_marker(self):
        with TemporaryDirectory() as directory,patch.dict(os.environ,{'LOCALAPPDATA':directory}),patch('sys.frozen',True,create=True),patch('sys.executable',str(Path(directory)/'app'/'RMAA+.exe')):
            self.assertEqual(preference_path(),Path(directory)/'RMAAOpenLab'/'preferences.json')
    def test_copy_and_relocation_preserve_settings_beside_exe(self):
        import shutil
        with TemporaryDirectory() as directory,patch('sys.frozen',True,create=True):
            first=Path(directory)/'first';first.mkdir();(first/'portable.flag').touch()
            with patch('sys.executable',str(first/'RMAA+.exe')):
                save_preferences({'language':'en','fs':'48000'})
                self.assertEqual(preference_path(),first/'data'/'preferences.json')
            second=Path(directory)/'copied';shutil.copytree(first,second)
            with patch('sys.executable',str(second/'RMAA+.exe')):
                self.assertEqual(load_preferences(),{'language':'en','fs':'48000'})
                save_preferences({'language':'ru'})
            with patch('sys.executable',str(first/'RMAA+.exe')):
                self.assertEqual(load_preferences()['language'],'en')
    def test_failed_portable_write_does_not_fall_back_to_profile(self):
        with TemporaryDirectory() as directory,patch('sys.frozen',True,create=True),patch('sys.executable',str(Path(directory)/'RMAA+.exe')),patch.dict(os.environ,{'LOCALAPPDATA':str(Path(directory)/'profile')}):
            (Path(directory)/'portable.flag').touch();(Path(directory)/'data').write_text('blocked')
            with self.assertRaises(OSError):save_preferences({'fs':'48000'})
            self.assertFalse((Path(directory)/'profile').exists())
if __name__=='__main__':unittest.main()
