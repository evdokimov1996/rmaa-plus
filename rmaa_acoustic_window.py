"""Acoustic tests desktop UI: relative sweep, THD-vs-frequency and THD-vs-level.

A certified microphone, safe test levels, and reference calibration are
required for quantitative SPL work. This application does not provide SPL.
"""
from __future__ import annotations

from rmaa_i18n import tr
import html
import json
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from rmaa_acoustic import AcousticConfig
from rmaa_plots import set_response_limits
from rmaa_acoustic_session import (
    acoustic_live, import_acoustic_files, save_acoustic_files,
)

class AcousticWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title(tr('RMAA Open Lab — акустические измерения'))
        self.geometry("1080x830")
        self.minsize(770,630)
        self.report=None
        self.events=queue.Queue()
        self.busy=False
        self.cancel_event=threading.Event()
        self.close_pending=False
        self.parent_app=parent
        self.rate=tk.StringVar(value=parent.fs_var.get() if hasattr(parent,'fs_var') else "48000")
        self.low=tk.StringVar(value="20")
        self.high=tk.StringVar(value="20000")
        self.seconds=tk.StringVar(value="4")
        self.level=tk.StringVar(value="0")
        self.mic=tk.StringVar()
        self.status=tk.StringVar(value=tr('Микрофонная коррекция необязательна. Значения АЧХ относительные, SPL не вычисляется.'))
        self.mode=tk.StringVar(value=parent.mode_var.get() if hasattr(parent,'mode_var') else tr('Автоматически'))
        self.pcm=tk.StringVar(value=parent.bits_var.get() if hasattr(parent,'bits_var') else "32-bit float")
        self.preset=tk.StringVar(value="standard")
        self.save_raw=tk.BooleanVar(value=bool(parent.save_wave_var.get()) if hasattr(parent,'save_wave_var') else False)
        self.graphs={}
        self._ui()
        self.protocol("WM_DELETE_WINDOW",self.close_window)

    def _ui(self):
        frame=ttk.Frame(self,padding=10)
        frame.pack(fill="both",expand=True)
        top=ttk.LabelFrame(frame,text=tr('Акустическая система / параметры теста'),padding=8)
        top.pack(fill="x")
        ttk.Label(top,text=tr('Режим')).grid(row=0,column=5,sticky="w")
        preset=ttk.Combobox(top,textvariable=self.preset,values=("standard","detailed","thorough","subwoofer"),state="readonly",width=14)
        preset.grid(row=1,column=5,padx=4)
        preset.bind("<<ComboboxSelected>>",self.apply_preset)
        fields=[
            (tr('Fs, Гц'),self.rate,("44100","48000","88200","96000","176400","192000","352800","384000")),
            (tr('Нижняя, Гц'),self.low,("10","20","30","40","50")),
            (tr('Верхняя, Гц'),self.high,("100","200","300","500","1000","10000","15000","18000","20000")),
            (tr('Длительность sweep, с'),self.seconds,("2","4","6","8","12")),
        ]
        for i,(name,var,values) in enumerate(fields):
            ttk.Label(top,text=name).grid(row=0,column=i,sticky="w",padx=4,pady=(0,2))
            ttk.Combobox(top,textvariable=var,values=values,state="readonly",width=15).grid(
                row=1,column=i,padx=4,sticky="ew"
            )
            top.columnconfigure(i,weight=1)
        ttk.Label(top,text=tr('Уровень, dBFS')).grid(row=0,column=4,sticky='w',padx=4)
        ttk.Label(top,text=tr('0 dBFS peak (без ослабления)')).grid(row=1,column=4,padx=4)
        ttk.Label(top,text=tr('Коррекция АЧХ микрофона (CSV, частота Гц; поправка dB):')).grid(
            row=2,column=0,columnspan=5,sticky="w",pady=(11,2))
        path=ttk.Frame(top)
        path.grid(row=3,column=0,columnspan=5,sticky="ew")
        ttk.Entry(path,textvariable=self.mic).pack(side="left",fill="x",expand=True)
        ttk.Button(path,text=tr('Выбрать CSV'),command=self.choose_mic).pack(side="left",padx=5)
        ttk.Button(path,text=tr('Очистить'),command=lambda:self.mic.set("")).pack(side="left")

        dev=ttk.LabelFrame(frame,text=tr('Устройства (для теста через звуковую карту)'),padding=8)
        dev.pack(fill="x",pady=6)
        ttk.Label(dev,text=tr('Воспроизведение')).grid(row=0,column=0,sticky="w")
        ttk.Label(dev,text=tr('Запись')).grid(row=0,column=1,sticky="w")
        self.outcombo=ttk.Combobox(dev,state="readonly",width=43)
        self.incombo=ttk.Combobox(dev,state="readonly",width=43)
        self.outcombo.grid(row=1,column=0,sticky="ew",padx=4)
        self.incombo.grid(row=1,column=1,sticky="ew",padx=4)
        dev.columnconfigure(0,weight=1)
        dev.columnconfigure(1,weight=1)
        ttk.Label(dev,text=tr('Аудио API:')).grid(row=2,column=0,sticky="w",pady=(7,0))
        ttk.Label(dev,text=tr('Формат потока:')).grid(row=2,column=1,sticky="w",pady=(7,0))
        ttk.Combobox(dev,textvariable=self.mode,state="readonly",
          values=[tr('Автоматически'),"MME","DirectSound","WASAPI Shared","WASAPI Exclusive","ASIO","Kernel Streaming"],
          width=23).grid(row=3,column=0,sticky="w")
        ttk.Combobox(dev,textvariable=self.pcm,state="readonly",
          values=["32-bit float","16-bit PCM","24-bit PCM","32-bit PCM"],
          width=23).grid(row=3,column=1,sticky="w")
        self.inputs=list(getattr(self.parent_app,"inputs",[]))
        self.outputs=list(getattr(self.parent_app,"outputs",[]))
        self.outcombo["values"]=[v[1] for v in self.outputs]
        self.incombo["values"]=[v[1] for v in self.inputs]
        if self.outputs:self.outcombo.current(max(0,self.parent_app.output_combo.current()) if hasattr(self.parent_app,'output_combo') else 0)
        if self.inputs:self.incombo.current(max(0,self.parent_app.input_combo.current()) if hasattr(self.parent_app,'input_combo') else 0)
        buttons=ttk.Frame(frame)
        buttons.pack(fill="x",pady=5)
        self.run_button=ttk.Button(buttons,text=tr('▶ Измерить'),command=self.run_live)
        self.run_button.pack(side="left",padx=(0,7))
        ttk.Button(buttons,text=tr('Стоп'),command=self.cancel_event.set).pack(side="left")
        self.generate_button=ttk.Button(buttons,text=tr('Создать автономный WAV'),command=self.save_stimulus)
        self.generate_button.pack(side="left",padx=7)
        self.import_button=ttk.Button(buttons,text=tr('Обработать записанный WAV'),command=self.open_capture)
        self.import_button.pack(side="left",padx=7)
        ttk.Button(buttons,text=tr('Отчёт JSON'),command=self.export_json).pack(side="right")
        ttk.Button(buttons,text=tr('График PNG'),command=self.export_png).pack(side="right",padx=5)
        ttk.Button(buttons,text=tr('Отчёт HTML'),command=self.export_html).pack(side="right",padx=5)
        ttk.Label(frame,textvariable=self.status,wraplength=950).pack(anchor="w",pady=(0,5))
        ttk.Checkbutton(frame,text=tr('Сохранить сырой WAV перед анализом'),variable=self.save_raw).pack(anchor="w")
        self.tabs=ttk.Notebook(frame)
        self.tabs.pack(fill="both",expand=True)
        for tabid,title in [
            ("response",tr('АЧХ')),("thdf",tr('THD от частоты')),
            ("thdl",tr('THD от уровня')),("phase",tr('Фаза'))
        ]:
            content=ttk.Frame(self.tabs)
            self.tabs.add(content,text=title)
            fig=Figure(figsize=(8.7,4.2),dpi=100)
            ax=fig.add_subplot(111)
            ax.grid(True,which="both",alpha=.22)
            ax.set_title(tr('Нет измерений'))
            canvas=FigureCanvasTkAgg(fig,master=content)
            canvas.get_tk_widget().pack(fill="both",expand=True)
            self.graphs[tabid]=(fig,ax,canvas)
        ttk.Label(frame,text=tr('Не подключайте выход усилителя к линейному или микрофонному входу. Чувствительность микрофона и абсолютный уровень SPL не калибруются.'),
                  wraplength=990,foreground="#666666").pack(anchor="w",pady=4)

    def apply_preset(self,event=None):
        sub=self.preset.get()=="subwoofer"
        self.low.set("10" if sub else "20")
        self.high.set("300" if sub else "20000")
        self.seconds.set({"standard":"4","detailed":"8","thorough":"12","subwoofer":"12"}[self.preset.get()])

    def _config(self):
        cfg=AcousticConfig(sample_rate=int(self.rate.get()),start_hz=float(self.low.get()),
             end_hz=float(self.high.get()),sweep_seconds=float(self.seconds.get()),
             level_dbfs=float(self.level.get()),detail=self.preset.get())
        cfg.validate()
        if self.mic.get().strip() and not Path(self.mic.get()).is_file():
            raise ValueError(tr('Не найден CSV коррекции микрофона'))
        return cfg

    def choose_mic(self):
        path=filedialog.askopenfilename(parent=self,title=tr('Коррекция АЧХ микрофона'),
                                        filetypes=[("CSV","*.csv"),(tr('Все файлы'),"*.*")])
        if path:self.mic.set(path)

    def _set_busy(self,value):
        self.busy=value
        for btn in (self.run_button,self.generate_button,self.import_button):
            btn.configure(state="disabled" if value else "normal")

    def _submit(self,kind,fn):
        if self.busy or getattr(self.parent_app,"busy",False):return
        self.cancel_event.clear()
        self.parent_app.busy=True
        self.parent_app.cancel_event=self.cancel_event
        self._set_busy(True)
        self.status.set(tr('Выполняется анализ акустического измерения…'))
        def job():
            try:
                self.events.put((kind,fn()))
            except Exception as err:
                self.events.put(("error",str(err)))
        threading.Thread(target=job,daemon=True).start()
        self.after(150,self._poll)

    def run_live(self):
        try:
            cfg=self._config()
            input_id=self.inputs[self.incombo.current()][0]
            output_id=self.outputs[self.outcombo.current()][0]
            mode=self.mode.get()
            pcm=self.pcm.get()
        except (ValueError,IndexError) as err:
            messagebox.showerror(tr('Параметры'),str(err),parent=self)
            return
        if not messagebox.askyesno(
            tr('Акустический тест'),
            tr('Внимание: приложение воспроизведёт sweep и серию тонов. Основной цифровой уровень 0 dBFS peak. Физическая громкость зависит от ручки усилителя. Убедитесь, что она снижена; рядом с динамиком не должно быть людей или животных. Начать?'),
            parent=self
        ):return
        mic_path=self.mic.get() or None
        stream_options=dict(getattr(self.parent_app,"stream_options",{}))
        recording_path=None
        if self.save_raw.get():
            recording_path=filedialog.asksaveasfilename(parent=self,title=tr('Сохранить измеренную запись WAV'),defaultextension='.wav',filetypes=[('WAV','*.wav')])
            if not recording_path:return
        self._submit("result",lambda:acoustic_live(
            input_id,output_id,cfg,mic_csv=mic_path,
            api_mode=mode,format_label=pcm,cancel=self.cancel_event,stream_options=stream_options,recording_path=recording_path))

    def save_stimulus(self):
        try: cfg=self._config()
        except Exception as exc:
            messagebox.showerror(tr('Параметры'),str(exc),parent=self)
            return
        folder=filedialog.askdirectory(parent=self,title=tr('Папка автономного акустического теста'))
        if not folder:return
        self._submit("saved",lambda:save_acoustic_files(folder,cfg))

    def open_capture(self):
        manifest=filedialog.askopenfilename(parent=self,title=tr('Файл manifest автономного теста'),
                                            filetypes=[("JSON","*.json")])
        if not manifest:return
        wave=filedialog.askopenfilename(parent=self,title=tr('Записанный акустический тест WAV'),
                                        filetypes=[("WAV","*.wav *.rf64")])
        if not wave:return
        mic_path=self.mic.get() or None
        self._submit("result",lambda:import_acoustic_files(
             wave,manifest,mic_csv=mic_path))

    def _poll(self):
        try:
            while True:
                event,value=self.events.get_nowait()
                self._set_busy(False)
                self.parent_app.busy=False
                if self.close_pending:
                    self.destroy()
                    return
                if self.cancel_event.is_set():
                    self.status.set(tr('Остановлено; аудиопоток освобождён.'))
                    continue
                if event=="error":
                    self.status.set(tr('Ошибка измерения'))
                    messagebox.showerror(tr('Акустика'),value,parent=self)
                elif event=="saved":
                    wav,meta=value
                    self.status.set(tr('Тестовая последовательность и описание сохранены.'))
                    messagebox.showinfo(tr('Автономная запись'),
                          tr('Созданы:\n{0}\n{1}\n\nПроиграйте WAV в исследуемой системе, запишите два канала с той же частотой и импортируйте запись.').format(wav,meta),
                          parent=self)
                elif event=="result":
                    self.set_result(value)
                    self.status.set(
                         tr('Измерение обработано. АЧХ относительная; SPL не вычисляется.'))
        except queue.Empty:
            pass
        if self.busy and self.winfo_exists():
            self.after(150,self._poll)

    def close_window(self):
        if self.busy:
            self.close_pending=True
            self.cancel_event.set()
        else:self.destroy()

    def set_result(self,report):
        self.report=report
        for key,(figure,ax,canvas) in self.graphs.items():
            ax.clear()
            for side,color,label in (
                 ("left","tab:blue",tr('Левый')),("right","tab:red",tr('Правый'))
            ):
                data=report[side]
                if not data:continue
                if key=="response":
                    response=data["response"]
                    ax.semilogx(response["frequency_hz"],response["response_db"],
                                color=color,label=label)
                    ax.set_ylabel(tr('Относительно {0:g} Гц, dB').format(response.get('reference_hz', 1000)))
                    ax.set_xlabel(tr('Гц'))
                elif key=="thdf":
                    rows=data["thd_frequency"]
                    ax.semilogx([r["frequency_hz"] for r in rows],
                                [r["thd_pct"] for r in rows],color=color,
                                marker=".",label=label)
                    ax.set_ylabel("THD, %")
                    ax.set_xlabel(tr('Частота, Гц'))
                elif key=="thdl":
                    rows=data["thd_level"]
                    ax.plot([r["nominal_level_dbfs"] for r in rows],
                            [r["thd_pct"] for r in rows],
                            color=color,marker=".",label=label)
                    ax.set_xlabel(tr('Цифровой уровень, dBFS'))
                    ax.set_ylabel("THD, %")
                elif key=="phase":
                    fr=data["response"]
                    ax.semilogx(fr["frequency_hz"],fr["phase_deg"],color=color,label=label)
                    ax.set_xlabel(tr('Частота, Гц'))
                    ax.set_ylabel(tr('Фаза, °'))
            if key=='response':set_response_limits(ax)
            ax.grid(True,which="both",alpha=.28)
            ax.legend(loc="best")
            ax.set_title({
                "response":tr('Акустическая АЧХ, относительная'),
                "thdf":tr('Гармонические искажения по частотам'),
                "thdl":tr('Гармонические искажения по уровням'),
                "phase":tr('Фазовая характеристика после выравнивания'),
            }[key])
            figure.tight_layout()
            canvas.draw_idle()

    def export_json(self):
        if self.report is None:
            messagebox.showinfo(tr('Результаты'),tr('Измерений пока нет'),parent=self)
            return
        path=filedialog.asksaveasfilename(parent=self,defaultextension=".json",
             filetypes=[("JSON","*.json")],initialfile="rmaa_acoustic_report.json")
        if path:
            Path(path).write_text(json.dumps(self.report,ensure_ascii=False,indent=2),
                                  encoding="utf-8")

    def export_png(self):
        if self.report is None:
            messagebox.showinfo(tr('Результаты'),tr('Измерений пока нет'),parent=self)
            return
        path=filedialog.asksaveasfilename(parent=self,defaultextension=".png",
             filetypes=[("PNG","*.png")],initialfile="acoustic_plot.png")
        if path:
            key=list(self.graphs)[self.tabs.index(self.tabs.select())]
            self.graphs[key][0].savefig(path,dpi=150)

    def export_html(self):
        if self.report is None:
            messagebox.showinfo(tr('Результаты'),tr('Измерений пока нет'),parent=self)
            return
        from base64 import b64encode
        from io import BytesIO
        imgs=[]
        for key,(figure,ax,canvas) in self.graphs.items():
            buffer=BytesIO()
            figure.savefig(buffer,format="png",dpi=115)
            encoded=b64encode(buffer.getvalue()).decode("ascii")
            imgs.append(f'<h2>{html.escape(ax.get_title())}</h2>'
                        f'<img style="width:100%;max-width:960px" '
                        f'src="data:image/png;base64,{encoded}">')
        page=(tr('<html lang="ru"><meta charset="utf-8"><style>body{{font-family:Segoe UI,Arial;max-width:990px;margin:40px auto;color:#222}}img{{height:auto}}p{{line-height:1.5}}</style><h1>RMAA Open Lab — акустический отчёт</h1><p>Экспериментальные относительные измерения, не эквивалентны RMAA. Абсолютное звуковое давление SPL не определено.</p><p>Частота дискретизации: {0} Гц. Источник: {1}</p>').format(self.report['fs'],html.escape(str(self.report['source'])))
              + "".join(imgs) + "</html>")
        path=filedialog.asksaveasfilename(parent=self,defaultextension=".html",
             filetypes=[("HTML","*.html")],initialfile="rmaa_acoustic_report.html")
        if path:Path(path).write_text(page,encoding="utf-8")
