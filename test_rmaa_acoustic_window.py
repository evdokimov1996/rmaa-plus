"""Acoustic UI smoke test — no sound is played or recorded."""
import unittest
from rmaa_acoustic import AcousticConfig
from rmaa_acoustic_session import create_acoustic_sequence, analyze_acoustic_capture
from rmaa_classic import Lab
from rmaa_acoustic_window import AcousticWindow

class AcousticUITest(unittest.TestCase):
    def test_open_and_plot_all_sections(self):
        cfg=AcousticConfig(sweep_seconds=1.3,tail_seconds=.25,level_dbfs=-24,end_hz=15000)
        x,manifest=create_acoustic_sequence(cfg,lite=True)
        report=analyze_acoustic_capture(x,manifest)
        app=Lab()
        app.withdraw()
        try:
            window=AcousticWindow(app)
            window.withdraw()
            window.set_result(report)
            window.update_idletasks()
            self.assertEqual(len(window.graphs),4)
            for _,(figure,ax,canvas) in window.graphs.items():
                self.assertGreater(len(ax.lines),0)
            window.destroy()
        finally: app.destroy()

if __name__=="__main__":unittest.main(verbosity=2)
