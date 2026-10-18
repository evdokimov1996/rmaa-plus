"""Explicit axis edits with per-plot drafts, persistent validation and no timers."""
import math
import tkinter as tk
from tkinter import ttk
from matplotlib.ticker import MultipleLocator, AutoLocator, AutoMinorLocator
from rmaa_i18n import tr
from rmaa_plots import set_response_limits


class AxisRangeControls(ttk.Frame):
    def __init__(self, parent, axes, canvas, toolbar):
        super().__init__(parent, padding=(6, 4))
        self.axes, self.canvas, self.toolbar = axes, canvas, toolbar
        self.key = None
        self.states = {}
        self._writing = False
        self.dirty = False
        self.vars = [tk.StringVar() for _ in range(4)]
        self.step = tk.StringVar()
        self.message = tk.StringVar()
        self.entries = []
        self.range_row = ttk.Frame(self)
        self.range_row.pack(fill='x')
        ttk.Label(self.range_row, text=tr('Диапазон Y, dB:')).pack(side='left', padx=6)
        for value in (.5, 1, 2, 3):
            ttk.Button(self.range_row, text=f'±{value:g}', width=6,
                       command=lambda span=value: self.preset(span)).pack(side='left', padx=2)
        ttk.Button(self.range_row, text=tr('Авто'), command=self.auto).pack(side='left', padx=2)
        ttk.Label(self.range_row, text=tr('Шаг сетки, dB:')).pack(side='left', padx=(12, 3))
        self.step_entry = ttk.Entry(self.range_row, textvariable=self.step, width=7)
        self.step_entry.pack(side='left')
        ttk.Label(self.range_row, text=tr('пусто = авто')).pack(side='left', padx=3)
        manual = ttk.Frame(self)
        manual.pack(fill='x', pady=(4, 0))
        for i, name in enumerate(('X min', 'X max', 'Y min', 'Y max')):
            ttk.Label(manual, text=name).pack(side='left', padx=(6, 2))
            entry = ttk.Entry(manual, textvariable=self.vars[i], width=9)
            entry.pack(side='left')
            self.entries.append(entry)
        self.apply_button = ttk.Button(manual, text=tr('Применить'), command=self.apply)
        self.apply_button.pack(side='left', padx=8)
        tk.Label(self, textvariable=self.message, anchor='w', foreground='#a62b21',
                 wraplength=900).pack(fill='x')
        for variable in (*self.vars, self.step):
            variable.trace_add('write', self._edited)
        for entry in (*self.entries, self.step_entry):
            entry.bind('<Return>', self._return)
            entry.bind('<KP_Enter>', self._return)
            entry.bind('<Control-a>', self._select_all)
            entry.bind('<Control-A>', self._select_all)
            entry.bind('<Control-KeyPress>', self._control_key)

    def _control_key(self,event):
        # On Windows the Russian layout sends Cyrillic_ef for physical A.
        # Honor the familiar shortcut by its virtual key in either layout.
        if event.keysym.lower() in ('a','cyrillic_ef') or (
                self.tk.call('tk','windowingsystem')=='win32' and event.keycode==65):
            return self._select_all(event)

    @staticmethod
    def _select_all(event):
        event.widget.selection_range(0,'end')
        event.widget.icursor('end')
        return 'break'

    def _return(self, event):
        self.apply()
        return 'break'

    def _edited(self, *args):
        if not self._writing:
            self.dirty = True

    def _write(self, values, step=''):
        self._writing = True
        try:
            for variable, value in zip(self.vars, values):
                if variable.get()!=value:variable.set(value)
            if self.step.get()!=step:self.step.set(step)
        finally:
            self._writing = False

    def _remember(self):
        if self.key is not None:
            self.states[self.key] = dict(values=[v.get() for v in self.vars], step=self.step.get(),
                                         dirty=self.dirty, message=self.message.get(),
                                         locator=self.axes.yaxis.get_major_locator())

    def activate(self, key, logarithmic=True, response=False):
        self.key, self.logarithmic, self.response = key, logarithmic, response
        if response:
            self.range_row.pack(fill='x', before=self.winfo_children()[1])
        else:
            self.range_row.pack_forget()
        state = self.states.get(key)
        if state:
            self._write(state['values'], state['step'])
            self.dirty = state['dirty']
            self.message.set(state['message'])
            self.axes.yaxis.set_major_locator(state['locator'])
        else:
            self.dirty = False
            self.message.set('')
            self._write([f'{v:.7g}' for v in (*self.axes.get_xlim(), *self.axes.get_ylim())], '0,5' if response else '')
            if response:
                self.axes.yaxis.set_major_locator(MultipleLocator(.5))
                self.axes.yaxis.set_minor_locator(AutoMinorLocator(2))

    def sync(self, event=None):
        # Redraw, focus loss and incoming samples never overwrite pending edits.
        if self.key is not None and not self.dirty:
            self._write([f'{v:.7g}' for v in (*self.axes.get_xlim(), *self.axes.get_ylim())], self.step.get())

    @staticmethod
    def number(value):
        result = float(value.strip().replace(',', '.'))
        if not math.isfinite(result) or abs(result) >= 1e12:
            raise ValueError()
        return result

    def apply(self):
        try:
            a, b, c, d = [self.number(v.get()) for v in self.vars]
            if a >= b or c >= d or (self.logarithmic and a <= 0):
                raise ValueError()
        except ValueError:
            self.message.set(tr('Некорректные границы осей. Минимум должен быть меньше максимума.'))
            return False
        try:
            step = self.number(self.step.get()) if self.response and self.step.get().strip() else None
            if step is not None and (step <= 0 or (d-c)/step > 500):
                raise ValueError()
        except ValueError:
            self.message.set(tr('Шаг сетки должен быть положительным; не более 500 делений.'))
            return False
        self.toolbar.push_current()
        self.axes.set_xlim(a, b)
        self.axes.set_ylim(c, d)
        if self.response:
            self.axes.yaxis.set_major_locator(MultipleLocator(step) if step else AutoLocator())
            self.axes.yaxis.set_minor_locator(AutoMinorLocator(2))
        self.toolbar.push_current()
        self.dirty = False
        self.message.set('')
        self.sync()
        self.canvas.draw_idle()
        return True

    def preset(self, span):
        # A preset acts immediately; unrelated X drafts are retained.
        self.vars[2].set(f'{-span:g}')
        self.vars[3].set(f'{span:g}')
        return self.apply()

    def auto(self):
        old = self.axes.get_ylim()
        set_response_limits(self.axes)
        target = self.axes.get_ylim()
        self.axes.set_ylim(*old)
        self.vars[2].set(f'{target[0]:g}')
        self.vars[3].set(f'{target[1]:g}')
        return self.apply()

    def reset(self):
        self.states.pop(self.key, None)
        self.dirty = False
        self.message.set('')
        self._write([f'{v:.7g}' for v in (*self.axes.get_xlim(), *self.axes.get_ylim())], '0,5' if self.response else '')
        if self.response:
            self.axes.yaxis.set_major_locator(MultipleLocator(.5))
            self.axes.yaxis.set_minor_locator(AutoMinorLocator(2))
