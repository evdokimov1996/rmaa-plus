"""UI smoke tests executed on the Windows desktop (without audio output)."""
import unittest
import tkinter as tk
from unittest.mock import patch
from rmaa_session import create_sequence, analyze_recording
from rmaa_classic import Lab
from rmaa_reports import report_html,report_txt

class WindowsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples,cls.manifest=create_sequence(48000,-18,lite=True)
        cls.data=analyze_recording(cls.samples,cls.manifest)
    def test_all_v4_windows_open(self):
        app=Lab()
        app.withdraw()
        callback_errors=[]
        app.report_callback_exception=lambda *args:callback_errors.append(args)
        try:
            app.report=self.data
            app.open_results()
            app.update()
            self.assertIsNotNone(app.results_window.chart)
            self.assertGreater(len(app.results_window.chart[0].figure.axes[0].lines),0)
            original_canvas=app.results_window.canvas
            app.results_window.select_plot('noise')
            app.update()
            self.assertIs(app.results_window.canvas,original_canvas)
            self.assertEqual(callback_errors,[])
            app.browse_results()
            app.analyze_wav()
            app.update_idletasks()
            app.update()
            self.assertGreaterEqual(len(app.winfo_children()),3)
            self.assertTrue(app.input_combo.winfo_exists())
        finally:
            app.destroy()
    def test_report_extra_metrics(self):
        content=report_html([("measurement",self.data)])
        self.assertIn("Динамический диапазон",content)
        self.assertIn("IMD SMPTE",content)
        self.assertIn("Шум A-weighted",content)
        self.assertIn("Взаимопроникновение",content)
        plaintext=report_txt([("measurement",self.data)])
        self.assertIn("IMD CCIF",plaintext)

    def test_opens_without_any_audio_devices(self):
        with patch('sounddevice.query_devices',return_value=[]),patch('sounddevice.query_hostapis',return_value=[]):
            app=Lab();app.withdraw()
            try:
                app.update();self.assertEqual(app.inputs,[]);self.assertEqual(app.outputs,[])
                self.assertTrue(app.go_button.winfo_exists())
            finally:app.destroy()

if __name__=="__main__":
    unittest.main(verbosity=2)
