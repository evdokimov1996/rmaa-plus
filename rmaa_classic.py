"""RMAA Open Lab: classic-style, independent audio analysis interface.

The UI follows the workflow of RightMark Audio Analyzer for familiarity.
It does not copy its source code, graphics, algorithms or resources.
"""
from __future__ import annotations

from rmaa_i18n import tr
from rmaa_version import VERSION

import os
os.environ.setdefault("SD_ENABLE_ASIO","1")
import json
import queue
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from audio_core import generate_tone, generate_sweep, save_json
from rmaa_session import duplex_test, save_offline, import_offline
from rmaa_devices import probe_duplex

TEST_ITEMS = [
    (tr('Частотная характеристика'), "response", True),
    (tr('Уровень шума'), "noise", True),
    (tr('Динамический диапазон (-60 dBFS)'), "dynamic_range", True),
    (tr('Гармонические искажения'), "thd", True),
    (tr('Гармонические искажения + шум'), "thdn", True),
    (tr('Интермодуляционные искажения (SMPTE)'), "imd_smpte", True),
    (tr('Интермодуляционные искажения (CCIF 19/20 кГц)'), "imd_ccif", True),
    (tr('Интермодуляционные искажения на 10 кГц'), "imd_10k", True),
    (tr('Интермодуляционные искажения (переменная частота)'), "imd_sweep", True),
    (tr('Взаимопроникновение каналов'), "crosstalk", True),
    (tr('Фаза и импульсная характеристика'), "phase", True),
    (tr('Анализ спектра WAV'), "spectrum", False),
]

class Lab(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'RMAA Open Lab v{VERSION} — '+tr('аудиоанализатор'))
        self.geometry("920x760")
        self.minsize(670, 570)
        self.inputs = []
        self.outputs = []
        self.events = queue.Queue()
        self.report = None
        self.tests = {}
        self.results_window = None
        self.busy = False
        self.cancel_event=threading.Event()
        self.poll_id=None
        self.stream_options={"input_map":(1,2),"output_map":(1,2),"blocksize":0,"latency":"high"}
        self.results_slots=[None]*8
        from rmaa_preferences import load_preferences
        self.preferences=load_preferences()
        from rmaa_i18n import get_language
        self.language=get_language()
        self._style()
        self._layout()
        self.refresh_devices()
        self.restore_preferences()
        self.protocol("WM_DELETE_WINDOW",self.close_app)

    def _style(self):
        style = ttk.Style()
        themes = style.theme_names()
        if "vista" in themes:
            style.theme_use("vista")
        elif "winnative" in themes:
            style.theme_use("winnative")
        style.configure("App.TFrame", padding=3)
        style.configure("Section.TLabelframe", padding=7)
        style.configure("Active.TButton", padding=(11, 5))
        style.configure("Result.TLabel", font=("Segoe UI", 9, "bold"))

    def _layout(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label=tr('Сохранить результаты…'), command=self.export_json)
        filemenu.add_command(label=tr('Сохранить тестовые WAV…'), command=self.export_wav)
        filemenu.add_command(label=tr('Создать полный автономный тест…'), command=self.export_offline_suite)
        filemenu.add_command(label=tr('Импорт записи автономного теста…'), command=self.import_offline_suite)
        filemenu.add_command(label=tr('Результаты и HTML/TXT-отчёт…'), command=self.browse_results)
        filemenu.add_command(label=tr('Анализ произвольного WAV…'), command=self.analyze_wav)
        filemenu.add_separator()
        filemenu.add_command(label=tr('Выход'), command=self.close_app)
        menubar.add_cascade(label=tr('Файл'), menu=filemenu)
        testmenu = tk.Menu(menubar, tearoff=0)
        testmenu.add_command(label=tr('Начать измерения'), command=self.begin)
        testmenu.add_command(label=tr('Калибровка уровня / полярности'),command=self.calibrate)
        testmenu.add_command(label=tr('Онлайн АЧХ / уровень'),command=self.open_live_monitor)
        testmenu.add_command(label=tr('Только воспроизведение'),command=lambda:self.external_mode("playback"))
        testmenu.add_command(label=tr('Только запись внешнего теста'),command=lambda:self.external_mode("recording"))
        testmenu.add_command(label=tr('Остановить'),command=self.stop)
        testmenu.add_command(label=tr('Создать автономный тест WAV + manifest'), command=self.export_offline_suite)
        testmenu.add_separator()
        testmenu.add_command(label=tr('Акустические тесты / THD по частотам'), command=self.open_acoustic_tests)
        testmenu.add_command(label=tr('Обработать внешнюю запись'), command=self.import_offline_suite)
        testmenu.add_command(label=tr('Показать результаты'), command=self.open_results)
        testmenu.add_command(label=tr('8 слотов / сравнение'), command=self.browse_results)
        testmenu.add_command(label=tr('Спектральный анализ WAV'), command=self.analyze_wav)
        testmenu.add_command(label=tr('Обновить аудиоустройства'), command=self.refresh_devices)
        testmenu.add_command(label=tr('Проверить доступные режимы'), command=self.probe_devices)
        menubar.add_cascade(label=tr('Тесты'), menu=testmenu)
        testmenu.add_command(label=tr('Пинг: реальное открытие выбранного режима'),command=self.ping_mode)
        testmenu.add_command(label=tr('Панель установленного ASIO-драйвера…'),command=self.asio_properties)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label=tr('Как пользоваться — F1'),command=self.open_help)
        helpmenu.add_command(label=tr('Проверить обновления…'),command=self.check_updates)
        helpmenu.add_command(label=tr('Исходный код на GitHub'),command=self.open_project)
        helpmenu.add_separator()
        helpmenu.add_command(label=tr('Об этой программе'), command=self.about)
        menubar.add_cascade(label=tr('Справка'), menu=helpmenu)
        self.bind('<F1>',lambda event:self.open_help())
        languages=tk.Menu(menubar,tearoff=False)
        languages.add_command(label="Русский",command=lambda:self.choose_language("ru"))
        languages.add_command(label="English",command=lambda:self.choose_language("en"))
        menubar.add_cascade(label="RU / EN",menu=languages)
        self.config(menu=menubar)

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        top = ttk.LabelFrame(root, text=tr('Устройства воспроизведения / записи и режимы'), padding=9)
        top.pack(fill="x")
        for i in range(5):
            top.columnconfigure(i, weight=(3 if i == 0 else 1 if i in (1,2,3) else 0))
        self.out_var = tk.StringVar()
        self.in_var = tk.StringVar()
        self.fs_var = tk.StringVar(value="48000")
        self.bits_var = tk.StringVar(value="32-bit float")
        self.mode_var = tk.StringVar(value=tr('Автоматически'))
        self.channels_var = tk.StringVar(value=tr('2 канала'))
        ttk.Label(top, text=tr('Воспроизведение')).grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Label(top, text=tr('Каналы')).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Label(top, text=tr('Формат данных')).grid(row=0, column=2, sticky="w", padx=5)
        ttk.Label(top, text=tr('Частота')).grid(row=0, column=3, sticky="w", padx=5)
        self.output_combo = ttk.Combobox(top, textvariable=self.out_var, state="readonly", width=35)
        self.output_combo.grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=(0, 7))
        channel_box=ttk.Combobox(top,textvariable=self.channels_var,values=[tr('2 канала'),tr('Моно (вход 1)')],state="readonly",width=14)
        channel_box.grid(row=1,column=1,sticky="ew",padx=5)
        channel_box.bind("<<ComboboxSelected>>",lambda _:self.stream_options.update(input_map=(1,1) if self.channels_var.get().startswith(tr('Моно')) else (1,2)))
        ttk.Combobox(top, textvariable=self.bits_var, values=["32-bit float", "8-bit PCM", "16-bit PCM", "24-bit PCM", "32-bit PCM"], state="readonly", width=13).grid(row=1, column=2, sticky="ew", padx=5)
        ttk.Combobox(top, textvariable=self.fs_var, values=["44100", "48000", "88200", "96000", "176400", "192000", "352800", "384000"], state="readonly", width=10).grid(row=1, column=3, sticky="ew", padx=5)
        ttk.Label(top, text=tr('Запись')).grid(row=2, column=0, sticky="w", padx=(0, 5))
        self.input_combo = ttk.Combobox(top, textvariable=self.in_var, state="readonly", width=35)
        self.input_combo.grid(row=3, column=0, sticky="ew", padx=(0, 5))
        ttk.Label(top, textvariable=self.channels_var).grid(row=3, column=1, sticky="w", padx=5)
        ttk.Label(top, textvariable=self.bits_var).grid(row=3, column=2, sticky="w", padx=5)
        ttk.Label(top, textvariable=self.fs_var).grid(row=3, column=3, sticky="w", padx=5)
        ttk.Button(top, text=tr('Обновить'), width=10, command=self.refresh_devices).grid(row=1, column=4, sticky="ew", padx=5)
        ttk.Button(top, text=tr('Настройки'), width=10, command=self.settings).grid(row=3, column=4, sticky="ew", padx=5)
        ttk.Label(top, text=tr('Аудио API:')).grid(row=4, column=0, sticky="w", padx=5, pady=(7, 1))
        ttk.Combobox(top, textvariable=self.mode_var, state="readonly", width=22,
                     values=[tr('Автоматически'), "MME", "DirectSound", "WASAPI Shared", "WASAPI Exclusive", "ASIO", "Kernel Streaming"]).grid(
                     row=5, column=0, sticky="w", padx=5, pady=(0, 4))
        ttk.Label(top, text=tr('ASIO/WASAPI Exclusive доступны только при наличии соответствующих устройств.'),
                  foreground="#666666").grid(row=5, column=1, columnspan=4, sticky="w", padx=5)

        tests = ttk.LabelFrame(root, text=tr('Тесты и настройки'), padding=(9, 9))
        tests.pack(fill="both", expand=True, pady=(10, 5))
        canvas = tk.Canvas(tests, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(tests, orient="vertical", command=canvas.yview)
        self.tests_inner = ttk.Frame(canvas)
        self.tests_inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.tests_inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for idx, (label, key, enabled) in enumerate(TEST_ITEMS):
            var = tk.BooleanVar(value=enabled)
            self.tests[key] = var
            row = ttk.Frame(self.tests_inner)
            row.pack(fill="x", anchor="w", pady=3)
            box = ttk.Checkbutton(row, text=label, variable=var)
            box.pack(side="left", anchor="w")
            if not enabled:
                box.configure(state="disabled")
                ttk.Label(row, text=tr('(планируется)'), foreground="#888888").pack(side="left", padx=12)
        foot = ttk.Frame(root)
        foot.pack(fill="x", pady=(2, 5))
        ttk.Button(foot, text=tr('Сброс настроек'), command=self.reset_options).pack(side="left")
        ttk.Button(foot,text=tr('Онлайн АЧХ / уровень'),command=self.open_live_monitor).pack(side="left",padx=6)
        ttk.Button(foot, text=tr('Акустика'), command=self.open_acoustic_tests).pack(side="right", padx=5)
        ttk.Button(foot, text=tr('Анализ WAV'), command=self.analyze_wav).pack(side="right", padx=5)
        ttk.Button(foot, text=tr('О программе'), command=self.about).pack(side="right")
        ttk.Button(foot, text=tr('8 слотов / отчёты'), command=self.browse_results).pack(side="right", padx=5)

        control = ttk.LabelFrame(root, text=tr('Начать тесты'), padding=9)
        control.pack(fill="x")
        ttk.Label(control, text=tr('Тестовый уровень:')).pack(side="left")
        self.level_var = tk.StringVar(value="0 dBFS")
        ttk.Label(control,text=tr("0 dBFS peak (без ослабления)")).pack(side="left",padx=8)
        self.go_button = ttk.Button(control, text=tr('▶  Начать тестирование'), style="Active.TButton", command=self.begin)
        self.go_button.pack(side="left", padx=12)
        ttk.Button(control,text=tr('■ Стоп'),command=self.stop).pack(side="left")
        ttk.Button(control, text=tr('Офлайн WAV'), command=self.export_offline_suite).pack(side="right")
        ttk.Button(control, text=tr('Импорт записи'), command=self.import_offline_suite).pack(side="right", padx=5)
        self.save_wave_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text=tr('Сохранить измеренный WAV и его манифест (для последующей проверки)'),
                        variable=self.save_wave_var).pack(anchor="w",pady=(3,0))
        self.progress=ttk.Progressbar(root,maximum=100)
        self.progress.pack(fill="x",pady=3)
        self.status = tk.StringVar(value=tr('Готово. Подключите Line Out → Line In и выберите устройства.'))
        ttk.Label(root, textvariable=self.status, wraplength=735).pack(fill="x", pady=8, anchor="w")
        ttk.Label(root, text=tr('Независимая экспериментальная реализация. Результаты пока не откалиброваны.'), foreground="#666666").pack(anchor="w")

    def refresh_devices(self):
        if self.busy:return
        previous_in=self.in_var.get()
        previous_out=self.out_var.get()
        try:
            import sounddevice as sd
            items = sd.query_devices()
            hosts = sd.query_hostapis()
            self.inputs = []
            self.outputs = []
            for index, d in enumerate(items):
                api = hosts[d["hostapi"]]["name"]
                label = f'{index}: [{api}] {d["name"]}'
                if d["max_input_channels"] >= 2:
                    self.inputs.append((index, label))
                if d["max_output_channels"] >= 2:
                    self.outputs.append((index, label))
            self.output_combo["values"] = [label for _, label in self.outputs]
            self.input_combo["values"] = [label for _, label in self.inputs]
            if self.outputs:
                self.output_combo.current(next((i for i,(_,label) in enumerate(self.outputs) if label==previous_out),0))
            if self.inputs:
                self.input_combo.current(next((i for i,(_,label) in enumerate(self.inputs) if label==previous_in),0))
            self.status.set(tr('Доступно: {0} выходов, {1} входов. Подключите безопасный аналоговый loopback.').format(len(self.outputs),len(self.inputs)))
        except Exception as err:
            self.status.set(tr('Ошибка чтения аудиоустройств: {0}').format(err))

    def choose_language(self,language):
        self.language=language
        self.persist_preferences()
        messagebox.showinfo("RU / EN","Language saved. Restart the application to apply.\nЯзык сохранён. Перезапустите приложение для применения.",parent=self)

    def open_live_monitor(self):
        current=getattr(self,'live_window',None)
        if current is not None and current.winfo_exists():current.lift();return
        if self.busy:return
        from rmaa_live_window import LiveWindow
        self.live_window=LiveWindow(self)

    def open_acoustic_tests(self):
        from rmaa_acoustic_window import AcousticWindow
        AcousticWindow(self)

    def analyze_wav(self):
        from rmaa_spectrum_window import SpectrumViewer
        SpectrumViewer(self)

    def browse_results(self):
        from rmaa_results_browser import ResultsBrowser
        ResultsBrowser(self, current=self.report)

    def reset_options(self):
        self.fs_var.set("48000")
        self.level_var.set("0 dBFS")
        self.mode_var.set(tr('Автоматически'))
        self.bits_var.set("32-bit float")
        self.stream_options={"input_map":(1,2),"output_map":(1,2),"blocksize":0,"latency":"high"}
        self.channels_var.set(tr('2 канала'))
        for _, key, available in TEST_ITEMS:
            self.tests[key].set(available)
        self.status.set(tr('Настройки измерений восстановлены.'))

    def probe_devices(self):
        try:
            inp=self.inputs[self.input_combo.current()][0]
            out=self.outputs[self.output_combo.current()][0]
        except (IndexError, ValueError):
            messagebox.showerror(tr('Устройства'), tr('Сначала выберите вход и выход.'))
            return
        if self.busy:
            return
        options=self.stream_options.copy()
        pcm=self.bits_var.get()
        mode=self.mode_var.get()
        self.busy=True
        self.status.set(tr('Проверка выбранного API, формата и каналов: 44,1–384 кГц…'))
        def job():
            try:
                result=probe_duplex(inp,out,format_label=pcm,mode=mode,**options)
                self.events.put(("diagnostics",result))
            except Exception as e:
                self.events.put(("error",str(e)))
        self.start_worker(job)

    def settings(self):
        if self.busy:return
        w=tk.Toplevel(self)
        w.title(tr('Настройки аудиопотока'))
        w.transient(self)
        frame=ttk.Frame(w,padding=16)
        frame.pack(fill="both",expand=True)
        variables={}
        fields=[("input_map",tr('Входные каналы L,R (1,1 для моно)'),",".join(map(str,self.stream_options["input_map"]))),
                ("output_map",tr('Выходные каналы L,R'),",".join(map(str,self.stream_options["output_map"]))),
                ("blocksize",tr('Буфер, кадров (0 = драйвер)'),str(self.stream_options["blocksize"])),
                ("latency",tr('Задержка high / low'),self.stream_options["latency"])]
        for row,(key,label,value) in enumerate(fields):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky="w",pady=6)
            var=tk.StringVar(value=value)
            variables[key]=var
            ttk.Entry(frame,textvariable=var,width=25).grid(row=row,column=1,padx=10)
        def apply():
            try:
                opts={key:tuple(int(n.strip()) for n in variables[key].get().split(",")) for key in ("input_map","output_map")}
                if any(len(v)!=2 or min(v)<1 or max(v)>64 for v in opts.values()):raise ValueError(tr('Каналы: две цифры от 1 до 64'))
                if len(set(opts["output_map"]))<2:raise ValueError(tr('Нужны два разных выходных канала'))
                opts["blocksize"]=int(variables["blocksize"].get())
                if not 0<=opts["blocksize"]<=65536:raise ValueError(tr('Буфер 0..65536'))
                opts["latency"]=variables["latency"].get()
                if opts["latency"] not in ("high","low"):raise ValueError(tr('Задержка high или low'))
                self.stream_options=opts
                self.channels_var.set(tr('Моно (вход 1)') if len(set(opts["input_map"]))==1 else tr('2 канала'))
                self.persist_preferences()
                w.destroy()
            except ValueError as exc:messagebox.showerror(tr('Настройки'),str(exc),parent=w)
        ttk.Button(frame,text=tr('Применить'),command=apply).grid(row=5,column=1,pady=10)
        ttk.Button(frame,text=tr('Проверить режимы'),command=self.probe_devices).grid(row=5,column=0)

    def asio_properties(self):
        if self.busy:return
        try:
            from rmaa_backend import show_asio_control_panel
            show_asio_control_panel(self.outputs[self.output_combo.current()][0],self.winfo_id())
        except (IndexError,ValueError,RuntimeError,OSError) as exc:
            messagebox.showerror("ASIO Properties",str(exc),parent=self)

    def about(self):
        messagebox.showinfo(
            "RMAA Open Lab", tr('RMAA Open Lab — независимый экспериментальный аудиоанализатор.\nСтруктура основного окна повторяет привычный рабочий процесс RMAA, но код, графики и методы измерений написаны отдельно.\nДо полного соответствия измерений оригиналу требуется дополнительная разработка.')
        )

    def export_offline_suite(self):
        if self.busy:
            return
        directory = filedialog.askdirectory(title=tr('Куда сохранить автономную тестовую последовательность'))
        if not directory:
            return
        try:
            fs=int(self.fs_var.get())
            level=float(self.level_var.get().split()[0])
            wav,meta=save_offline(directory,fs,level,selected_tests=self.selected_tests(),profile='precision')
            self.status.set(tr('Готово: {0} и {1}. Проиграйте WAV и запишите на проверяемом устройстве.').format(wav.name,meta.name))
            messagebox.showinfo(tr('Автономные тесты'),
                 tr('Созданы:\n{0}\n{1}\n\nПроиграйте файл на проверяемом устройстве и запишите его в стерео WAV с той же частотой. Затем выберите «Импорт записи автономного теста».').format(wav,meta))
        except Exception as err:
            messagebox.showerror(tr('Автономные тесты'),str(err))

    def import_offline_suite(self):
        if self.busy:
            return
        manifest=filedialog.askopenfilename(title=tr('Выберите manifest теста'),filetypes=[("JSON","*.json")])
        if not manifest: return
        wave=filedialog.askopenfilename(title=tr('Выберите записанный тестовый WAV'),filetypes=[("WAV","*.wav *.rf64")])
        if not wave: return
        selected=self.selected_tests()
        self.busy=True
        self.go_button.configure(state="disabled")
        self.status.set(tr('Проверяется WAV и выполняются измерения…'))
        def job():
            try:
                result=import_offline(wave,manifest,cancel=self.cancel_event,progress=self.report_progress)

                self.events.put(("success",result))
            except Exception as exc:
                self.events.put(("error",str(exc)))
        self.start_worker(job)

    def begin(self):
        if self.busy:
            return
        selected = [key for _, key, available in TEST_ITEMS if available and self.tests[key].get()]
        if not selected:
            messagebox.showinfo(tr('Тесты'), tr('Отметьте хотя бы один доступный тест.'))
            return
        try:
            input_id = self.inputs[self.input_combo.current()][0]
            output_id = self.outputs[self.output_combo.current()][0]
            fs = int(self.fs_var.get())
            level = float(self.level_var.get().split()[0])
            mode = self.mode_var.get()
            pcm = self.bits_var.get()
        except (IndexError, ValueError):
            messagebox.showerror(tr('Устройства'), tr('Выберите вход, выход, частоту и уровень.'))
            return
        if not messagebox.askyesno(
            tr('Проверка аудиотракта'),
            tr('Подключён безопасный кабель линейный выход → линейный вход?\n\nНе подключайте усиленный выход к входу звуковой карты. Во время теста будут воспроизводиться звуковые сигналы.')
        ):
            return
        recording_path=None
        if self.save_wave_var.get():
            recording_path=filedialog.asksaveasfilename(
                title=tr('Сохранить измеренную запись WAV'),defaultextension=".wav",
                initialfile="rmaa_measured.wav",filetypes=[("WAV","*.wav")])
            if not recording_path:
                return
        options=self.stream_options.copy()
        self.busy = True
        self.go_button.configure(state="disabled")
        self.status.set(tr('Выполняются выбранные измерения…'))
        def job():
            try:
                result = duplex_test(input_id, output_id, fs, level,
                                     api_mode=mode, format_label=pcm,
                                     recording_path=recording_path,selected_tests=selected,cancel=self.cancel_event,profile='precision',
                                     progress=self.report_progress,**options)
                result["selected_tests"] = selected
                self.events.put(("success", result))
            except Exception as err:
                self.events.put(("error", str(err)))
        self.start_worker(job)

    def restore_preferences(self):
        data=self.preferences
        for key,var in (("fs",self.fs_var),("format",self.bits_var),("mode",self.mode_var)):
            if isinstance(data.get(key),str):var.set(data[key])
        if self.fs_var.get() not in ("44100","48000","88200","96000","176400","192000","352800","384000"):self.fs_var.set("48000")
        self.level_var.set("0 dBFS")  # Old preferences must not restore digital attenuation.
        from rmaa_backend import PCM_FORMATS,MODES
        if self.bits_var.get() not in PCM_FORMATS:self.bits_var.set("32-bit float")
        if self.mode_var.get() not in MODES:self.mode_var.set(tr('Автоматически'))
        for combo,items,key in ((self.input_combo,self.inputs,"input"),(self.output_combo,self.outputs,"output")):
            for i,(_,label) in enumerate(items):
                if label.split(": ",1)[-1]==data.get(key):combo.current(i)
        opts=data.get("stream",{})
        if isinstance(opts,dict):
            for key in ("input_map","output_map"):
                values=opts.get(key)
                if isinstance(values,list) and len(values)==2 and all(type(n)==int and 1<=n<=64 for n in values):
                    if key=="input_map" or len(set(values))==2:self.stream_options[key]=tuple(values)
            size=opts.get("blocksize")
            if type(size)==int and 0<=size<=65536:self.stream_options["blocksize"]=size
            if opts.get("latency") in ("high","low"):self.stream_options["latency"]=opts["latency"]
        chosen=data.get("tests")
        if isinstance(chosen,list):
            for key,var in self.tests.items():var.set(key in chosen)
        self.save_wave_var.set(bool(data.get("save_wave",False)))
        self.channels_var.set(tr('Моно (вход 1)') if len(set(self.stream_options["input_map"]))==1 else tr('2 канала'))

    def persist_preferences(self):
        from rmaa_preferences import save_preferences
        data={"fs":self.fs_var.get(),"level":self.level_var.get(),"format":self.bits_var.get(),
              "mode":self.mode_var.get(),"input":self.in_var.get().split(": ",1)[-1],
              "output":self.out_var.get().split(": ",1)[-1],"stream":self.stream_options,
              "tests":self.selected_tests(),"save_wave":bool(self.save_wave_var.get()),"language":self.language}
        try:save_preferences(data)
        except OSError as exc:self.status.set(tr('Не удалось сохранить настройки: ')+str(exc))

    def calibrate(self):
        if self.busy:return
        try:
            inp=self.inputs[self.input_combo.current()][0]
            out=self.outputs[self.output_combo.current()][0]
            fs=int(self.fs_var.get())
            level=float(self.level_var.get().split()[0])
            mode=self.mode_var.get();pcm=self.bits_var.get()
            opts=self.stream_options.copy()
        except (IndexError,ValueError):return
        if not messagebox.askyesno(tr('Калибровка'),tr('Подключён безопасный линейный loopback? Будет короткий тестовый сигнал.'),parent=self):return
        def job():
            try:
                import numpy as np
                from rmaa_session import _probe,find_recording_offset
                from rmaa_backend import playrec_with_format
                from rmaa_dsp import db
                marker=_probe(fs,level,round(.32*fs))
                wave=np.vstack((np.zeros((fs//4,2)),np.column_stack((marker,marker)),np.zeros((fs,2))))
                manifest={"fs":fs,"level_dbfs":level,"marker_frames":len(marker),"marker_offset_frames":fs//4}
                captured=playrec_with_format(wave,fs,inp,out,mode=mode,format_label=pcm,
                    cancel=self.cancel_event,progress=self.report_progress,**opts)
                offset,quality=find_recording_offset(captured,manifest)
                start=offset+fs//4
                gain=[float(np.dot(captured[start:start+len(marker),c],marker)/np.dot(marker,marker)) for c in (0,1)]
                peak=db(np.max(np.abs(captured)))
                text=tr('Пик записи: {0:.2f} dBFS; задержка {1:.2f} мс; сходство {2:.3f}\n').format(peak,offset * 1000 / fs,quality)
                text+="\n".join(tr('{0}: усиление {1:+.2f} dB; полярность ').format(label,db(abs(value)))+(tr('нормальная') if value>=0 else tr('инвертирована')) for label,value in zip(("L","R"),gain))
                text+=tr('\nКлиппинг: ')+(tr('ДА, снизьте усиление') if peak>=-.2 else tr('не обнаружен'))
                self.events.put(("info",text))
            except Exception as exc:self.events.put(("error",str(exc)))
        self.start_worker(job)

    def external_mode(self,kind):
        if self.busy:return
        manifest_path=filedialog.askopenfilename(parent=self,title=tr('Манифест автономного теста'),filetypes=[("JSON","*.json")])
        if not manifest_path:return
        try:
            from rmaa_session import validate_manifest
            manifest=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            validate_manifest(manifest)
            fs=int(manifest["fs"])
            mode=self.mode_var.get();pcm=self.bits_var.get()
            opts=self.stream_options.copy()
            from rmaa_backend import PCM_FORMATS
            dtype=PCM_FORMATS[pcm]
            import numpy as np
            if kind=="playback":
                inp=None
                out=self.outputs[self.output_combo.current()][0]
                source=filedialog.askopenfilename(parent=self,title=tr('Тестовый WAV для воспроизведения'),filetypes=[("WAV","*.wav")])
                if not source:return
                import soundfile as sf
                info=sf.info(source)
                if info.samplerate!=fs or info.channels!=2 or info.frames!=manifest["frames"]:raise ValueError(tr('WAV не соответствует манифесту'))
                audio,_=sf.read(source,dtype="float32",always_2d=True)
                target=None
            else:
                inp=self.inputs[self.input_combo.current()][0]
                out=None
                target=filedialog.asksaveasfilename(parent=self,title=tr('Сохранить внешнюю запись'),defaultextension=".wav")
                if not target:return
                if Path(target).exists() or Path(target).with_suffix(".json").exists():raise ValueError(tr('Выберите новое имя записи и манифеста'))
                manifest["input_map"]=list(opts["input_map"])
                manifest["output_map"]=list(opts["output_map"])
                audio=np.zeros((manifest["frames"]+round(7*fs),2),dtype=np.float32)
        except Exception as exc:
            messagebox.showerror(tr('Внешний тест'),str(exc),parent=self)
            return
        if not messagebox.askyesno(tr('Внешний тест'),tr('Начать? Для записи включите внешний тестовый сигнал в течение первых 7 секунд; для воспроизведения проверьте подключение и уровень.'),parent=self):return
        def job():
            try:
                from rmaa_transport import transfer
                from rmaa_backend import api_for_device,MODES
                import sounddevice as sd
                dev=inp if inp is not None else out
                api=api_for_device(dev)
                tokens={"MME":"MME","DirectSound":"DirectSound","WASAPI Shared":"WASAPI","WASAPI Exclusive":"WASAPI","ASIO":"ASIO","Kernel Streaming":"WDM-KS"}
                if mode not in MODES or (mode in tokens and tokens[mode] not in api):raise ValueError(tr('Выбранное устройство не соответствует API'))
                extra=sd.WasapiSettings(exclusive=True) if mode=="WASAPI Exclusive" else None
                captured=transfer(audio,fs,inp,out,dtype=dtype,extra_settings=extra,
                    cancel=self.cancel_event,progress=self.report_progress,**opts)
                if target:
                    import soundfile as sf
                    sf.write(target,captured,fs,subtype="FLOAT")
                    Path(target).with_suffix(".json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
                    from rmaa_session import analyze_recording
                    result=analyze_recording(captured,manifest,cancel=self.cancel_event)
                    result.update(saved_recording=target,saved_manifest=str(Path(target).with_suffix(".json")))
                    self.events.put(("success",result))
                else:self.events.put(("info",tr('Воспроизведение завершено; внешняя запись выполняется выбранным вами устройством.')))
            except Exception as exc:self.events.put(("error",str(exc)))
        self.start_worker(job)

    def ping_mode(self):
        if self.busy:return
        try:
            inp=self.inputs[self.input_combo.current()][0];out=self.outputs[self.output_combo.current()][0]
            fs=int(self.fs_var.get());mode=self.mode_var.get();pcm=self.bits_var.get();opts=self.stream_options.copy()
        except (IndexError,ValueError):return
        def job():
            try:
                import numpy as np
                from rmaa_backend import playrec_with_format
                playrec_with_format(np.zeros((round(.3*fs),2),np.float32),fs,inp,out,mode=mode,format_label=pcm,cancel=self.cancel_event,**opts)
                self.events.put(("info",tr('Режим открыт и закрыт: {0} Гц, {1}, {2}. Цифровая тишина. Это не проверка bit-perfect.').format(fs,pcm,mode)))
            except Exception as exc:self.events.put(("error",str(exc)))
        self.start_worker(job)

    def selected_tests(self):
        return [key for _,key,available in TEST_ITEMS if available and self.tests[key].get()]

    def open_help(self):
        from rmaa_help import HelpWindow
        window=getattr(self,'help_window',None)
        if window is not None and window.winfo_exists():window.lift();return
        self.help_window=HelpWindow(self)

    def open_project(self):
        import webbrowser
        from rmaa_updates import PROJECT_URL
        webbrowser.open(PROJECT_URL)

    def check_updates(self):
        if self.busy:
            messagebox.showinfo(tr('Обновление'),tr('Сначала завершите текущую операцию.'),parent=self);return
        from rmaa_updates import fetch_latest
        def job():
            try:self.events.put(('update_checked',fetch_latest(VERSION,self.cancel_event)))
            except Exception as error:self.events.put(('update_error',str(error)))
        self.status.set(tr('Проверка обновлений на GitHub…'));self.start_worker(job,kind='update')

    def download_update(self,release):
        from rmaa_updates import prepare_update
        if not getattr(__import__('sys'),'frozen',False):
            messagebox.showinfo(tr('Обновление'),tr('Для исходников используйте git pull и установку зависимостей. Перезапуск доступен в переносимой Windows-сборке.'),parent=self);return
        def job():
            try:self.events.put(('update_ready',str(prepare_update(release,self.cancel_event,self.report_progress))))
            except Exception as error:self.events.put(('update_error',str(error)))
        self.status.set(tr('Загрузка и проверка обновления…'));self.start_worker(job,kind='update')

    def install_update(self,work):
        from rmaa_updates import launch_installer
        from rmaa_preferences import preference_path
        import sys
        if not messagebox.askyesno(tr('Обновление'),tr('Архив проверен. Закрыть RMAA+, установить обновление и запустить новую версию? Настройки сохранятся.'),parent=self):
            from rmaa_updates import _owned_remove,cache_root
            _owned_remove(Path(work),cache_root());self.status.set(tr('Установка отменена.'));return
        try:
            if self.report:
                recovery=preference_path().parent/('update-recovery-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
                recovery.parent.mkdir(parents=True,exist_ok=True)
                with recovery.open('x',encoding='utf8') as file:json.dump(self.report,file,ensure_ascii=False)
            self.persist_preferences()
            launch_installer(work,Path(sys.executable).resolve().parent)
        except Exception as error:
            messagebox.showerror(tr('Обновление'),str(error),parent=self);return
        self.destroy()

    def start_worker(self,job,kind='measurement'):
        self.task_kind=kind
        self.cancel_event.clear()
        self.busy=True
        self.go_button.configure(state="disabled")
        self.progress["value"]=0
        self.persist_preferences()
        threading.Thread(target=job,daemon=True).start()
        if self.poll_id is None:self.poll_id=self.after(100,self._check_events)

    def report_progress(self,value):
        self.events.put(("progress",value))

    def stop(self):
        if self.busy:
            self.cancel_event.set()
            self.status.set(tr('Остановка загрузки…') if getattr(self,'task_kind','')=='update' else tr('Остановка и освобождение аудиопотока…'))

    def close_app(self):
        if self.busy:
            self.stop()
            self.status.set(tr('Остановка… После завершения можно закрыть окно.'))
            return
        self.persist_preferences()
        if self.poll_id is not None:self.after_cancel(self.poll_id)
        self.destroy()

    def _check_events(self):
        self.poll_id=None
        try:
            while True:
                event,item=self.events.get_nowait()
                if event=="progress":
                    self.progress["value"]=100*item
                    continue
                self.busy=False
                self.go_button.configure(state="normal")
                if self.cancel_event.is_set():
                    if event=='update_ready':
                        from rmaa_updates import _owned_remove,cache_root
                        _owned_remove(Path(item),cache_root())
                    self.status.set(tr('Операция обновления остановлена.') if getattr(self,'task_kind','')=='update' else tr('Остановлено. Аудиопоток освобождён.'))
                    continue
                if event=='update_checked':
                    if item is None:
                        self.status.set(tr('Установлена последняя версия.'))
                        messagebox.showinfo(tr('Обновление'),tr('Установлена последняя версия.')+' '+VERSION,parent=self)
                    elif messagebox.askyesno(tr('Обновление'),tr('Доступна версия {0}. Скачать обновление с GitHub?').format(item['version']),parent=self):self.download_update(item)
                    else:self.status.set(tr('Загрузка не начата.'))
                elif event=='update_ready':self.install_update(item)
                elif event=='update_error':
                    self.status.set(tr('Обновление не выполнено. Текущая версия сохранена.'))
                    messagebox.showerror(tr('Обновление'),str(item),parent=self)
                elif event=="diagnostics":
                    lines=[tr('Вход: ')+item["input_name"],tr('Выход: ')+item["output_name"],item["dtype"]]
                    for entry in item["rates"]:
                        lines.append(tr('{0} Гц: вход {1}; выход {2}').format(entry['fs'],entry['input'],entry['output']))
                    lines.append(tr('Проверка параметров не доказывает стабильность или bit-perfect работу.'))
                    messagebox.showinfo(tr('Доступные режимы'), "\n".join(lines),parent=self)
                    self.status.set(tr('Диагностика завершена.'))
                elif event=="success":
                    self.report=item
                    self.status.set(tr('Выбранные измерения завершены.'))
                    self.open_results()
                    rejected=[item[side]['response'].get('reason','') for side in ('left','right')
                              if item.get(side,{}).get('response',{}).get('valid') is False]
                    if rejected:
                        self.status.set(tr('АЧХ не прошла проверку; остальные измерения сохранены.'))
                        messagebox.showwarning(tr('АЧХ: измерение недостоверно'),'\n'.join(rejected),parent=self.results_window)
                elif event=="info":
                    self.status.set(str(item))
                    messagebox.showinfo(tr('Измерение'),str(item),parent=self)
                else:
                    self.status.set(tr('Измерение не выполнено; предыдущий результат сохранён.'))
                    messagebox.showerror(tr('Измерение не выполнено'),str(item),parent=self)
        except queue.Empty:pass
        if self.busy and self.poll_id is None:self.poll_id=self.after(100,self._check_events)

    def open_results(self):
        if not self.report:
            messagebox.showinfo(tr('Результаты'),tr('Измерений пока нет.'),parent=self)
            return
        from rmaa_result_window import ResultWindow
        if self.results_window is not None and self.results_window.winfo_exists():
            self.results_window.set_results([(tr('Текущий'),self.report)])
            self.results_window.deiconify();self.results_window.lift()
        else:self.results_window=ResultWindow(self,[(tr('Текущий'),self.report)])

    def export_json(self):
        if not self.report:
            messagebox.showinfo(tr('Нет данных'), tr('Сначала проведите измерение.'))
            return
        filename = "rmaa-open-lab-" + datetime.now().strftime("%Y%m%d-%H%M") + ".json"
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            initialfile=filename,
                                            filetypes=[("JSON files", "*.json")])
        if path:
            save_json(path, self.report)
            self.status.set(tr('Сохранено: {0}').format(path))

    def export_wav(self):
        import soundfile as sf
        directory = filedialog.askdirectory(title=tr('Куда сохранить тестовые аудиофайлы'))
        if not directory:
            return
        fs = int(self.fs_var.get())
        level = float(self.level_var.get().split()[0])
        sf.write(str(Path(directory) / "rmaa-tone-997Hz.wav"),
                 generate_tone(fs, seconds=3.0, level_dbfs=level), fs, subtype="PCM_24")
        sf.write(str(Path(directory) / "rmaa-sweep.wav"),
                 generate_sweep(fs, seconds=4.0, level_dbfs=level), fs, subtype="PCM_24")
        self.status.set(tr('Созданы тестовые WAV: {0}').format(directory))

if __name__ == "__main__":
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='--apply-update':
        from rmaa_updates import apply_update_main
        sys.exit(apply_update_main(sys.argv[2]))
    elif len(sys.argv)>1 and sys.argv[1]=='--recover-update':
        from rmaa_updates import recover_update
        sys.exit(recover_update(sys.argv[2]))
    elif len(sys.argv)>1 and sys.argv[1]=='--update-self-test':
        expected,receipt,nonce=sys.argv[2:]
        if expected!=VERSION:sys.exit(2)
        from rmaa_result_window import ResultWindow
        root=tk.Tk();root.withdraw()
        window=ResultWindow(root,[('Startup',{'fs':48000,'left':{},'right':{}})]);root.update();window.destroy();root.destroy()
        with Path(receipt).open('x',encoding='utf8') as file:json.dump({'version':VERSION,'nonce':nonce,'healthy':True},file)
    else:
        if len(sys.argv)>1 and sys.argv[1]=='--cleanup-update':
            from rmaa_updates import cleanup_after_update
            try:cleanup_after_update(sys.argv[2],int(sys.argv[3]))
            except Exception:pass # Preserve failed cleanup; never prevent the app opening.
        Lab().mainloop()
