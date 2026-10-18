"""Viewer interaction invariants, with real Tk/Matplotlib and no audio output."""
import unittest
from types import SimpleNamespace
import tkinter as tk
from rmaa_result_window import ResultWindow


def fixture(right=False):
    side={'response':{'frequency_hz':[20,100,1000,10000,20000],'response_db':[-.1,-.02,0,.02,-.01],
                      'min_db':-.02,'max_db':.02,'min_20_20000_db':-.1,'max_20_20000_db':.02},
          'noise':{'noise_dbfs_rms':-105,'spectrum':{'frequency_hz':[20,100,1000,10000,20000],'amplitude_dbfs_peak':[-130,-125,-131,-134,-140]}},
          'tone':{'thd_pct':.001,'harmonic_frequencies_hz':[2000,3000],'harmonic_levels_db':[-100,-110],
                  'spectrum':{'frequency_hz':[20,1000,2000,10000],'amplitude_dbfs_peak':[-130,-12,-110,-140]}}}
    return {'fs':48000,'left':side,'right':side.copy() if right else {},'calibration':{'clock_drift_ppm':4.2}}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.errors=[]
        self.root.report_callback_exception=lambda *error:self.errors.append(error)
        self.root.tk.createcommand('bgerror',lambda message:self.errors.append(message))
        self.window=ResultWindow(self.root,[('Reference',fixture())]);self.root.update()
    def tearDown(self):
        self.window.destroy();self.root.update()
        self.assertFalse(self.errors)
        self.root.destroy()
    def test_mono_default_is_graph_with_one_real_numeric_column(self):
        w=self.window
        self.assertEqual(w.selected_key,'response')
        self.assertEqual(len(w.metric_table['columns']),1)
        self.assertEqual(str(w.channel_buttons['R']['state']),'disabled')
        self.assertEqual(len(w.axes.lines),1)
        self.assertEqual(w.axis_controls.step.get(),'0,5')
        self.assertEqual([tick for tick in w.axes.get_yticks() if -.5<=tick<=.5],[-.5,0,.5])
        self.assertFalse(self.errors)
    def test_channel_toggle_preserves_zoom_and_metric_switch_restores_it(self):
        w=self.window;canvas=w.canvas
        w.axes.set_xlim(80,8000);w.axes.set_ylim(-.04,.04)
        w.records[0].set(False);w._refresh_visibility();self.root.update()
        self.assertEqual(w.axes.get_xlim(),(80,8000))
        self.assertFalse(w._lines[0][2].get_visible())
        w.records[0].set(True);w._refresh_visibility()
        w.select_plot('noise');w.select_plot('tone');w.select_plot('response');self.root.update()
        self.assertIs(w.canvas,canvas)
        self.assertEqual(w.axes.get_xlim(),(80,8000))
        self.assertEqual(w.axes.get_ylim(),(-.04,.04))
        self.assertFalse(self.errors)

    def test_precision_warning_remains_visible_during_mouse_motion_and_zoom(self):
        data=fixture();data['left']['tone']['thd_pct']=27.
        data['left']['response'].update(valid=True,quality_flags=['noise_or_phase_realization_spread_limits_precision'],
            quality_summary={'realization_spread_p95_db':.2956,'local_noise_standard_error_median_db':.0292})
        w=self.window;w.set_results([('Bad capture',data)]);self.root.update()
        text=w.quality_message.get()
        self.assertIn('0.2956',text);self.assertIn('27.00%',text)
        self.assertTrue(w.quality_banner.winfo_ismapped())
        w._motion(SimpleNamespace(inaxes=w.axes,xdata=1000,ydata=0))
        w.axis_controls.preset(2);self.root.update()
        self.assertEqual(w.quality_message.get(),text)
        self.assertTrue(w.quality_banner.winfo_ismapped())
        w.set_results([('Good capture',fixture())]);self.root.update()
        self.assertFalse(w.quality_message.get())
        self.assertFalse(w.quality_banner.winfo_ismapped())

    def test_rejected_clock_is_visible_without_a_curve_and_exported_in_reports(self):
        from rmaa_reports import report_html,report_txt
        data=fixture();data['left']['response']={'valid':False,'frequency_hz':[],
            'response_db':[],'reason':'Residual clock drift 0.005105 ppm exceeds limit 0.005000 ppm'}
        w=self.window;w.set_results([('Rejected',data)]);self.root.update()
        self.assertTrue(w.quality_message.get());self.assertEqual(len(w.axes.lines),0)
        self.assertIn('0.005105',w.quality_message.get())
        self.assertIn('0.005105',report_html([('Rejected',data)]))
        self.assertIn('0.005105',report_txt([('Rejected',data)]))
    def test_probe_reports_actual_nearest_sample_and_can_be_replaced(self):
        w=self.window
        for x in (970,1050):w._probe(SimpleNamespace(inaxes=w.axes,button=1,xdata=x))
        self.root.update()
        rows=w.probe_table.get_children()
        self.assertEqual(len(rows),1)
        self.assertEqual(float(w.probe_table.item(rows[0],'values')[0]),1000)
        self.assertEqual(len(w._probe_artists),2)
        self.assertFalse(self.errors)
    def test_eight_records_and_stereo_are_distinct_and_harmonics_visible(self):
        w=self.window;w.set_results([(f'Set {i}',fixture(True)) for i in range(8)])
        w.select_plot('tone');self.root.update()
        self.assertEqual(len(w.metric_table['columns']),16)
        self.assertEqual(len(w.axes.lines),16)
        self.assertEqual(len(w.harmonic_table.get_children()),32)
        w.channels['R'].set(False);w._refresh_visibility()
        self.assertEqual(len(w.metric_table['columns']),8)
        self.assertFalse(self.errors)
    def test_range_draft_survives_draw_focus_and_plot_switch(self):
        w=self.window;c=w.axis_controls
        c.vars[2].set('-2,5');c.vars[3].set('2,5')
        w.canvas.draw_idle();w.graph_title.focus_force()
        self.root.after(30,self.root.quit);self.root.mainloop()
        self.assertEqual(c.vars[2].get(),'-2,5')
        w.select_plot('noise');self.root.update();w.select_plot('response');self.root.update()
        self.assertEqual(c.vars[2].get(),'-2,5')
        self.assertTrue(c.dirty)
        c.entries[3].focus_force();self.root.update();c.entries[3].event_generate('<Return>');self.root.update()
        self.assertEqual(w.axes.get_ylim(),(-2.5,2.5))
        self.assertFalse(c.dirty)
        w.select_plot('noise');w.select_plot('response');self.root.update()
        self.assertEqual(w.axes.get_ylim(),(-2.5,2.5))

    def test_invalid_range_is_visible_and_motion_cannot_clear_error(self):
        w=self.window;c=w.axis_controls;before=w.axes.get_ylim()
        c.vars[2].set('2');c.vars[3].set('2')
        self.assertFalse(w.apply_ranges());error=c.message.get()
        self.assertTrue(error)
        w._motion(SimpleNamespace(inaxes=w.axes,xdata=1000,ydata=0))
        w.canvas.draw_idle();self.root.update()
        self.assertEqual(c.message.get(),error)
        self.assertEqual(c.vars[2].get(),'2')
        self.assertEqual(w.axes.get_ylim(),before)
        c.vars[2].set('-0,5');c.vars[3].set('0,5');c.step.set('0,5')
        c.apply_button.focus_force();self.root.update();c.apply_button.invoke()
        self.root.after(30,self.root.quit);self.root.mainloop()
        self.assertEqual(w.axes.get_ylim(),(-.5,.5));self.assertFalse(c.message.get())
        ticks=w.axes.get_yticks();self.assertAlmostEqual(ticks[1]-ticks[0],.5)

    def test_windows_ctrl_a_selects_entire_numeric_entry(self):
        c=self.window.axis_controls
        for entry in (*c.entries,c.step_entry):
            entry.focus_force();self.root.update()
            if self.root.tk.call('tk','windowingsystem')=='win32':
                entry.event_generate('<KeyPress>',keycode=65,state=4)
            else:entry.event_generate('<Control-a>')
            self.root.update()
            self.assertTrue(entry.selection_present())
            self.assertEqual(entry.index('sel.first'),0)
            self.assertEqual(entry.index('sel.last'),len(entry.get()))
            self.window.canvas.draw_idle();self.root.update()
            self.assertTrue(entry.selection_present())

    def test_presets_auto_and_invalid_grid(self):
        w=self.window;c=w.axis_controls
        for span in (.5,1,2,3):
            self.assertTrue(c.preset(span));self.root.update()
            self.assertEqual(w.axes.get_ylim(),(-span,span))
            ticks=w.axes.get_yticks();self.assertAlmostEqual(ticks[1]-ticks[0],.5)
        self.assertTrue(c.auto());self.assertEqual(w.axes.get_ylim(),(-.5,.5))
        c.step.set('0');self.assertFalse(c.apply());self.assertTrue(c.message.get())
        c.step.set('0.000001');self.assertFalse(c.apply())
        c.step.set('');self.assertTrue(c.apply())

    def test_manual_two_db_range_and_reset_keep_half_db_grid(self):
        w=self.window;c=w.axis_controls
        c.vars[2].set('-2,0');c.vars[3].set('2,0');c.step.set('0,5')
        c.entries[3].focus_force();self.root.update();c.entries[3].event_generate('<Return>')
        self.root.after(30,self.root.quit);self.root.mainloop()
        self.assertEqual(w.axes.get_ylim(),(-2,2))
        ticks=w.axes.get_yticks();self.assertAlmostEqual(ticks[1]-ticks[0],.5)
        c.step.set('0,25');self.assertTrue(c.apply())
        w.select_plot('noise');w.select_plot('response');self.root.update()
        self.assertEqual(c.step.get(),'0,25')
        ticks=w.axes.get_yticks();self.assertAlmostEqual(ticks[1]-ticks[0],.25)
        w.reset_view();self.root.update()
        self.assertEqual(c.step.get(),'0,5')
        self.assertEqual([tick for tick in w.axes.get_yticks() if -.5<=tick<=.5],[-.5,0,.5])

    def test_live_controls_keep_user_range_and_draft_on_new_frames(self):
        from rmaa_live_window import LiveWindow
        live=LiveWindow(self.root)
        try:
            c=live.axis_controls;self.root.update()
            self.assertEqual(live.axes.get_ylim(),(-.5,.5))
            self.assertEqual(c.step.get(),'0,5')
            self.assertEqual([tick for tick in live.axes.get_yticks() if -.5<=tick<=.5],[-.5,0,.5])
            self.assertTrue(c.preset(2))
            c.vars[2].set('-1,5')
            for i in range(3):
                live.axes.plot([20,1000,20000],[0,.1,-.1]);live.canvas.draw_idle();self.root.update()
            self.assertEqual(c.vars[2].get(),'-1,5')
            self.assertEqual(live.axes.get_ylim(),(-2,2))
            c.vars[3].set('1,5');self.assertTrue(c.apply());self.root.update()
            self.assertEqual(live.axes.get_ylim(),(-1.5,1.5))
            self.assertIsNone(live.poll_id)
        finally:
            live.destroy();self.root.update()

    def test_resize_and_close_after_queued_redraw_has_no_callback_error(self):
        w=self.window;w.geometry('950x650+20+20');self.root.update()
        for entry in w.entries:self.assertLess(entry.winfo_rooty()+entry.winfo_height(),w.winfo_rooty()+w.winfo_height())
        w.select_plot('noise');w.destroy();self.root.update()
        self.assertFalse(self.errors)


if __name__=='__main__':unittest.main()
