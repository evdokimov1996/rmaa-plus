"""Live window ownership and active-only scheduling, with no audio devices."""
import tkinter as tk
from tkinter import ttk
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from rmaa_live_window import LiveWindow

class FakeMonitor:
    def __init__(self,*args):
        self.done=Event();self.stop_event=Event();self.channels=1;self.fs=48000
        self.format_label='24-bit PCM';self.mode='WASAPI Exclusive';self.error=None
    def start(self):pass
    def latest(self):return None
    def stop(self):self.stop_event.set()

class LiveWindowTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();p=self.root
        self.errors=[];p.report_callback_exception=lambda *e:self.errors.append(e)
        p.tk.createcommand('bgerror',lambda e:self.errors.append(e))
        p.busy=False;p.go_button=ttk.Button(p)
        p.inputs=[(1,'input')];p.outputs=[(2,'output')]
        p.input_combo=SimpleNamespace(current=lambda:0);p.output_combo=p.input_combo
        for name,value in [('fs_var','48000'),('mode_var','WASAPI Exclusive'),('bits_var','24-bit PCM'),('out_var','output'),('in_var','input')]:setattr(p,name,tk.StringVar(p,value=value))
        p.stream_options={'input_map':(1,1)}
        self.window=LiveWindow(p);p.update()
    def tearDown(self):
        self.window.destroy();self.root.update();self.root.destroy()
        self.assertFalse(self.errors)
    def test_idle_window_does_not_release_other_measurement_or_poll(self):
        w=self.window;self.assertIsNone(w.poll_id)
        self.root.busy=True;self.root.go_button.configure(state='disabled')
        w.destroy()
        self.assertTrue(self.root.busy)
        self.assertEqual(str(self.root.go_button['state']),'disabled')
    def test_done_stops_polling_and_restart_owns_new_service(self):
        w=self.window
        with patch('rmaa_live_window.LiveMonitor',FakeMonitor):
            w.start_monitor();first=w.monitor
            self.assertTrue(self.root.busy);self.assertIsNotNone(w.poll_id)
            first.done.set();w.after_cancel(w.poll_id);w._poll()
            self.assertFalse(self.root.busy);self.assertIsNone(w.poll_id)
            w.start_monitor();self.assertIsNot(w.monitor,first)
            self.assertTrue(self.root.busy)
    def test_close_requests_stop_and_waits_for_stream_release(self):
        w=self.window
        with patch('rmaa_live_window.LiveMonitor',FakeMonitor):w.start_monitor()
        monitor=w.monitor;w.close_window()
        self.assertTrue(monitor.stop_event.is_set());self.assertTrue(w.winfo_exists())
        monitor.done.set();w.after_cancel(w.poll_id);w._poll()
        self.assertFalse(w.winfo_exists());self.assertFalse(self.root.busy)

if __name__=='__main__':unittest.main()
