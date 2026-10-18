"""Pre-test live response and meters; updates exist only during an active stream."""
import time
import tkinter as tk
from tkinter import ttk,messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
from rmaa_i18n import tr
from rmaa_plots import draw_plot,TECHNICAL_COLORS
from rmaa_live import LiveMonitor
from rmaa_plot_lifecycle import cancel_canvas_callbacks
from rmaa_axis_controls import AxisRangeControls


class LiveWindow(tk.Toplevel):
    def __init__(self,parent):
        super().__init__(parent)
        self.parent_app=parent;self.monitor=None;self.poll_id=None;self.close_pending=False;self.owns_parent=False;self._closed=False
        self.stop_started=None;self.sequence=0;self.fft_id=0;self.lines=[];self.hold=[]
        self.title(tr('Онлайн АЧХ / уровень'))
        width=min(1160,self.winfo_screenwidth()-80);height=min(860,self.winfo_screenheight()-150)
        self.geometry(f'{width}x{height}+{max(20,(self.winfo_screenwidth()-width)//2)}+25')
        self.minsize(800,580)
        self.level=tk.StringVar(value='0')
        self.status=tk.StringVar(value=tr('Генератор 0 dBFS peak. Нажмите «Запустить»; подстройте входной уровень в тракте.'))
        self._ui();self.protocol('WM_DELETE_WINDOW',self.close_window)

    def _ui(self):
        head=ttk.Frame(self,padding=10);head.pack(fill='x')
        self.start_button=ttk.Button(head,text=tr('Запустить'),command=self.start_monitor);self.start_button.pack(side='left')
        self.stop_button=ttk.Button(head,text=tr('Стоп'),command=self.stop_monitor,state='disabled');self.stop_button.pack(side='left',padx=6)
        ttk.Label(head,text=tr('Генератор, dBFS peak:')).pack(side='left',padx=(20,5))
        ttk.Label(head,text='0').pack(side='left')
        ttk.Button(head,text=tr('Сброс пика'),command=self.reset_hold).pack(side='right')
        self.device_text=ttk.Label(self,padding=(10,0,10,5),wraplength=1080);self.device_text.pack(fill='x')
        self.meters=ttk.Frame(self,padding=(10,5));self.meters.pack(fill='x');self.meter_widgets=[]
        frame=ttk.Frame(self);frame.pack(fill='both',expand=True,padx=10)
        self.figure=Figure(figsize=(10,5),dpi=100,facecolor='#141c18');self.axes=self.figure.add_subplot(111)
        self.figure.subplots_adjust(left=.085,right=.98,bottom=.13,top=.91)
        draw_plot(self.axes,[],'response',technical=True)
        self.axes.set_xlim(20,20000);self.axes.set_ylim(-.5,.5)
        self.axes.set_title(tr('Предварительная АЧХ — ожидание сигнала'))
        self.canvas=FigureCanvasTkAgg(self.figure,master=frame)
        self.toolbar=NavigationToolbar2Tk(self.canvas,frame,pack_toolbar=False);self.toolbar.pack(side='bottom',fill='x')
        self.canvas.get_tk_widget().pack(fill='both',expand=True)
        self.axis_controls=AxisRangeControls(self,self.axes,self.canvas,self.toolbar)
        self.axis_controls.pack(fill='x',padx=6)
        self.axis_controls.activate('response',True,True)
        self._range_draw_id=self.canvas.mpl_connect('draw_event',self.axis_controls.sync)
        ttk.Label(self,textvariable=self.status,padding=(10,7),wraplength=1080).pack(fill='x')
        ttk.Label(self,text=tr('Живой multitone: АЧХ относительно 1 кГц. Для настройки тракта; итоговые показатели вычисляются отдельным тестом.'),padding=(10,0,10,8),wraplength=1080).pack(fill='x')

    def start_monitor(self):
        if self.monitor is not None and not self.monitor.done.is_set():return
        if self.parent_app.busy:return
        try:
            p=self.parent_app
            if p.input_combo.current()<0 or p.output_combo.current()<0:raise ValueError(tr('Выберите вход, выход, частоту и уровень.'))
            monitor=LiveMonitor(p.inputs[p.input_combo.current()][0],p.outputs[p.output_combo.current()][0],int(p.fs_var.get()),p.mode_var.get(),p.bits_var.get(),dict(p.stream_options),float(self.level.get()))
        except Exception as exc:messagebox.showerror(tr('Онлайн АЧХ / уровень'),str(exc),parent=self);return
        self.monitor=monitor;self.sequence=0;self.fft_id=0;self.stop_started=None;self.close_pending=False
        p.busy=True;self.owns_parent=True;p.cancel_event=monitor.stop_event;p.go_button.configure(state='disabled')
        self.start_button.configure(state='disabled');self.stop_button.configure(state='normal')
        self.device_text.configure(text=p.out_var.get()+' → '+p.in_var.get()+f'  ·  {monitor.fs} Hz  ·  '+monitor.format_label+'  ·  '+monitor.mode)
        for widget in self.meters.winfo_children():widget.destroy()
        self.meter_widgets=[];self.hold=[-150.]*monitor.channels
        for i in range(monitor.channels):
            row=ttk.Frame(self.meters);row.pack(fill='x',pady=2)
            ttk.Label(row,text='L' if i==0 else 'R',width=3,font=('Segoe UI',12,'bold')).pack(side='left')
            meter=tk.Canvas(row,height=22,width=400,background='#102217',highlightthickness=0);meter.pack(side='left',fill='x',expand=True)
            value=ttk.Label(row,text='—',width=40,font=('Consolas',11));value.pack(side='left',padx=12)
            self.meter_widgets.append((meter,value))
        for line in self.lines:line.remove()
        self.lines=[]
        for i in range(monitor.channels):
            line,=self.axes.plot([],[],color=TECHNICAL_COLORS[i],linewidth=1.4,label='L' if i==0 else 'R');self.lines.append(line)
        self.axes.set_title(tr('Предварительная АЧХ — сбор данных'))
        legend=self.axes.legend(facecolor='#14231a',loc='upper right')
        for text in legend.get_texts():text.set_color('#e4eee6')
        self.canvas.draw_idle();self.status.set(tr('Потоки запускаются…'))
        monitor.start();self.poll_id=self.after(100,self._poll)

    def reset_hold(self):self.hold=[-150.]*len(self.meter_widgets)

    def stop_monitor(self):
        if self.monitor is None:return
        self.monitor.stop()
        if self.stop_started is None:self.stop_started=time.monotonic()
        self.status.set(tr('Остановка и освобождение аудиопотока…'));self.stop_button.configure(state='disabled')

    def _poll(self):
        self.poll_id=None;monitor=self.monitor;value=monitor.latest()
        if value is not None and value['sequence']!=self.sequence:
            self.sequence=value['sequence']
            for i,(meter,label) in enumerate(self.meter_widgets):
                peak=float(value['peak_dbfs'][i]);rms=float(value['rms_dbfs'][i]);self.hold[i]=max(self.hold[i],peak)
                width=max(1,meter.winfo_width());meter.delete('all')
                limit=max(0.,min(1.,(peak+80)/80))*width
                meter.create_rectangle(0,0,limit,22,fill='#f14d47' if value['clipping'][i] else '#8ad24c',outline='')
                for db in (-60,-40,-20,-6):
                    x=(db+80)/80*width;meter.create_line(x,0,x,22,fill='#263d2d')
                label.configure(text=f'RMS {rms:6.1f}   Peak {peak:6.1f} dBFS')
            response=value.get('response')
            if response is not None and value['fft_id']!=self.fft_id:
                self.fft_id=value['fft_id']
                for i,line in enumerate(self.lines):line.set_data(response['frequency_hz'],response['response_db'][:,i])
                self.axes.set_title(tr('Предварительная АЧХ • относительно 1 кГц'));self.canvas.draw_idle()
            if self.stop_started is None:
                if any(value['clipping']):self.status.set(tr('КЛИППИНГ: уменьшите входной уровень.'))
                elif value['settling']:self.status.set(tr('Стабилизация после изменения уровня…'))
                elif response is None or not all(response['signal_found']):self.status.set(tr('Тестовый сигнал не обнаружен на всех выбранных входах. Проверьте соединение.'))
                else:self.status.set(tr('Сигнал найден. Подстройте тракт; затем остановите монитор и запустите измерение.')+'  '+tr('Максимальный пик: ')+', '.join(f'{v:.1f} dBFS' for v in self.hold))
        if monitor.done.is_set():
            self._release_parent()
            self.start_button.configure(state='normal');self.stop_button.configure(state='disabled')
            self.status.set(tr('Ошибка измерения')+': '+monitor.error if monitor.error else tr('Остановлено. Аудиопоток освобождён.'))
            if self.close_pending:self.destroy()
            return
        if self.stop_started is not None and time.monotonic()-self.stop_started>5:
            # The worker retains its shared lease while a driver is stuck. New
            # measurements cannot overlap it even if this window is closed.
            self._release_parent()
            self.status.set(tr('Драйвер не освободил поток. Новые сеансы заблокированы до его возврата или перезапуска программы.'))
            if self.close_pending:self.destroy()
            return
        self.poll_id=self.after(100,self._poll)

    def close_window(self):
        if self.monitor is not None and not self.monitor.done.is_set():self.close_pending=True;self.stop_monitor()
        else:self.destroy()

    def _release_parent(self):
        if not self.owns_parent:return
        self.owns_parent=False
        try:
            self.parent_app.busy=False
            if self.parent_app.go_button.winfo_exists():self.parent_app.go_button.configure(state='normal')
        except tk.TclError:pass

    def destroy(self):
        if self._closed:return
        self._closed=True
        if self.monitor is not None:self.monitor.stop()
        if self.poll_id is not None:self.after_cancel(self.poll_id);self.poll_id=None
        self._release_parent()
        self.monitor=None
        self.canvas.mpl_disconnect(self._range_draw_id)
        self.figure.clear();cancel_canvas_callbacks(self.canvas);super().destroy()
