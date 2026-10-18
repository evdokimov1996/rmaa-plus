import tkinter as tk
import unittest
from unittest.mock import patch
from rmaa_help import HelpWindow,RU,EN

class HelpTests(unittest.TestCase):
    def test_offline_guide_has_matching_sections_and_essential_setup(self):
        self.assertEqual(len(RU),8);self.assertEqual(len(EN),8)
        ru=' '.join(body for title,body in RU)
        for text in ('0 dBFS','0,5','Клиппинг','WASAPI','JSON','SHA-256','--recover-update'):self.assertIn(text,ru)

    def test_both_languages_navigation_and_readonly_text(self):
        root=tk.Tk();root.withdraw()
        try:
            for language in ('ru','en'):
                with patch('rmaa_help.get_language',return_value=language):w=HelpWindow(root)
                root.update();self.assertEqual(str(w.text['state']),'disabled')
                first=w.text.get('1.0','end');w.move(1);root.update();self.assertNotEqual(first,w.text.get('1.0','end'))
                for i in range(10):w.move(1)
                self.assertEqual(w.nav.curselection(),(7,))
                w.destroy();root.update()
        finally:root.destroy()
