"""English display must not change audio API identifiers or result keys."""
import os
import subprocess
import sys
import unittest


class LanguageTests(unittest.TestCase):
    def test_english_windows_and_reports_preserve_protocol_values(self):
        code=r'''
import re
from unittest.mock import patch
from rmaa_classic import Lab
from rmaa_acoustic_window import AcousticWindow
from rmaa_spectrum_window import SpectrumViewer
from rmaa_results_browser import ResultsBrowser
from rmaa_reports import report_html
from rmaa_backend import validate_mode
from rmaa_live_window import LiveWindow
from rmaa_result_window import ResultWindow
app=Lab();app.withdraw()
errors=[]
app.report_callback_exception=lambda *args:errors.append(args)
try:
    windows=[AcousticWindow(app),SpectrumViewer(app),ResultsBrowser(app),LiveWindow(app),ResultWindow(app,[('Reference',{'fs':48000,'left':{'noise':{'noise_dbfs_rms':-100}},'right':{}})])]
    for window in windows:window.withdraw()
    assert app.channels_var.get() in ('2 channels','Mono (input 1)')
    assert app.mode_var.get() in ('Automatic','WASAPI Exclusive','MME','ASIO','WASAPI Shared','DirectSound','Kernel Streaming')
    with patch('rmaa_backend.api_for_device',return_value='MME'):
        validate_mode(1,2,'Automatic')
    windows[0].preset.set('subwoofer');windows[0].apply_preset()
    assert windows[0]._config().end_hz==300
    page=report_html([('Synthetic',{'fs':48000,'left':{},'right':{}})])
    assert 'lang="en"' in page
    assert 'Measurement comparison' in page and 'Print / save PDF' in page
    assert not re.search('[А-Яа-я]',page)
    def inspect(widget):
        try:
            if 'text' in widget.keys():
                assert not re.search('[А-Яа-я]',str(widget.cget('text'))),widget.cget('text')
        except AttributeError:pass
        for child in widget.winfo_children():inspect(child)
    inspect(app)
    app.update()
    assert not errors,errors
finally:app.destroy()
'''
        result=subprocess.run([sys.executable,'-c',code],env={**os.environ,'RMAA_LANGUAGE':'en'},
                              capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
