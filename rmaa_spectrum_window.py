"""Standalone multi-window spectrum viewer for RMAA Open Lab.

All displayed values are exploratory estimates; not calibrated like RMAA.
"""
from __future__ import annotations

from rmaa_i18n import tr

import threading
import queue
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
from matplotlib.figure import Figure

from spectrum_tools import SpectrumOptions, spectrum_wav, hz_to_mel, mel_to_hz, audio_file_info


class SpectrumViewer(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title(tr('RMAA Open Lab — анализ спектра WAV'))
        self.geometry("1060x760")
        self.minsize(760, 530)
        self.current = None
        self.work_queue = queue.Queue()
        self.file = tk.StringVar()
        self.fft_size = tk.StringVar(value="16384")
        self.window_type = tk.StringVar(value="kaiser")
        self.overlap = tk.StringVar(value="75")
        self.beta = tk.StringVar(value="9")
        self.pad = tk.StringVar(value="1")
        self.channel = tk.StringVar(value="1")
        self.scale = tk.StringVar(value=tr('Логарифмическая'))
        self.make_ui()
        self.scale.trace_add("write",lambda *_:self.draw_plot())


    def make_ui(self):
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=tr('Файл WAV:')).pack(side="left")
        ttk.Entry(row, textvariable=self.file).pack(side="left", expand=True, fill="x", padx=8)
        ttk.Button(row, text=tr('Обзор…'), command=self.choose).pack(side="left")
        opts = ttk.LabelFrame(frame, text=tr('Параметры спектрального анализа'), padding=8)
        opts.pack(fill="x", pady=(8, 5))
        labels = [
            (tr('Размер БПФ'), self.fft_size, ["1024","2048","4096","8192","16384","32768","65536","131072","262144","524288","1048576","2097152","4194304","8388608"]),
            (tr('Окно'), self.window_type, ["kaiser","hann","hamming","blackman","blackmanharris","boxcar"]),
            ("Overlap, %", self.overlap, ["0","25","50","75","87.5"]),
            ("Zero padding", self.pad, ["1","2","4"]),
            (tr('Канал'), self.channel, [str(i) for i in range(1, 9)]),
            ("Kaiser β", self.beta, ["5","7","9","13","15"]),
            (tr('Шкала'), self.scale, [tr('Логарифмическая'),tr('Линейная'),tr('Мел')]),
        ]
        for i, (label, var, values) in enumerate(labels):
            j = i // 4
            k = i % 4
            ttk.Label(opts, text=label).grid(row=j*2, column=k, padx=6, sticky="w")
            ttk.Combobox(opts, textvariable=var, values=values,
                         state="readonly", width=18).grid(row=j*2+1, column=k, padx=6, pady=(1, 4), sticky="ew")
        for col in range(4):
            opts.columnconfigure(col, weight=1)
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=3)
        self.analyze_button = ttk.Button(row2, text=tr('▶  Анализировать'), command=self.begin)
        self.analyze_button.pack(side="left")
        ttk.Button(row2, text=tr('Сохранить PNG'), command=self.save_image).pack(side="left", padx=8)
        self.status_var = tk.StringVar(value=tr('Загрузите WAV. БПФ до 8M точек, при достаточной длине файла.'))
        ttk.Label(row2, textvariable=self.status_var).pack(side="left", padx=12)
        self.fig = Figure(figsize=(9, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title(tr('Спектр — нет данных'))
        self.ax.set_xlabel(tr('Частота, Гц'))
        self.ax.set_ylabel(tr('Уровень, dBFS (peak-ref)'))
        self.ax.grid(True, alpha=.3)
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, pady=(6, 0))
        self.toolbar=NavigationToolbar2Tk(self.canvas,frame)
        self.detail = tk.StringVar(value=tr('Уровень в dBFS/peak вычисляется после поправки на окно, не является измерением RMS.'))
        ttk.Label(frame, textvariable=self.detail, wraplength=950).pack(fill="x", pady=5)

    def choose(self):
        file = filedialog.askopenfilename(
            title=tr('Выберите файл WAV для анализа'),
            filetypes=[("WAV, RF64", "*.wav *.rf64"), (tr('Любые файлы'), "*.*")]
        )
        if file:
            self.file.set(file)
            try:
                info = audio_file_info(file)
                self.detail.set(
                    tr('Файл: {0} Гц; {1} канал(ов); {2}; {3:.2f} с.').format(info['samplerate'],info['channels'],info['subtype'],info['duration_seconds'])
                )
            except Exception as exc:
                self.detail.set(str(exc))

    def begin(self):
        try:
            opts = SpectrumOptions(
                fft_size=int(self.fft_size.get()),
                overlap_percent=float(self.overlap.get()),
                window=self.window_type.get(),
                kaiser_beta=float(self.beta.get()),
                zero_padding=int(self.pad.get())
            )
            opts.validate()
            channel = int(self.channel.get()) - 1
            path = self.file.get()
            if not Path(path).is_file():
                raise ValueError(tr('Выберите существующий WAV'))
        except Exception as exc:
            messagebox.showerror(tr('Параметры'), str(exc), parent=self)
            return
        self.analyze_button.configure(state="disabled")
        self.status_var.set(tr('Анализ файла выполняется…'))
        def worker():
            try:
                self.work_queue.put(("done", spectrum_wav(path, opts, channel)))
            except Exception as exc:
                self.work_queue.put(("error", str(exc)))
        self.working=True
        threading.Thread(target=worker, daemon=True).start()
        self.after(120,self.poll_queue)

    def poll_queue(self):
        try:
            while True:
                kind, data = self.work_queue.get_nowait()
                self.working=False
                self.analyze_button.configure(state="normal")
                if kind == "done":
                    self.current = data
                    self.draw_plot()
                    self.status_var.set(tr('Анализ завершён'))
                else:
                    self.status_var.set(tr('Ошибка анализа'))
                    messagebox.showerror(tr('Ошибка анализа WAV'), data, parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists() and getattr(self,"working",False):
            self.after(120, self.poll_queue)

    def draw_plot(self):
        result = self.current
        if result is None:
            return
        self.ax.clear()
        frequencies = result["frequency_hz"]
        levels = result["amplitude_dbfs_peak"]
        self.ax.plot(frequencies, levels, linewidth=.75)
        self.ax.set_ylim(min(-20, max(-240, result["peak_amplitude_dbfs"] - 120)),
                         min(20, result["peak_amplitude_dbfs"] + 10))
        mode = self.scale.get()
        if mode == tr('Логарифмическая'):
            self.ax.set_xscale("log")
            self.ax.set_xlim(20, max(100, float(frequencies[-1])))
        elif mode == tr('Мел'):
            self.ax.set_xscale("function", functions=(hz_to_mel, mel_to_hz))
            self.ax.set_xlim(0, float(frequencies[-1]))
        else:
            self.ax.set_xscale("linear")
            self.ax.set_xlim(0, float(frequencies[-1]))
        self.ax.set_title(tr('Спектр: канал {0}').format(result['channel'] + 1))
        self.ax.set_xlabel(tr('Частота, Гц'))
        self.ax.set_ylabel(tr('Амплитуда, дБFS (peak-ref)'))
        from matplotlib.ticker import MultipleLocator
        self.ax.yaxis.set_major_locator(MultipleLocator(10))
        self.ax.grid(True, which="both", alpha=.25)
        self.fig.tight_layout()
        self.canvas.draw_idle()
        self.detail.set(
            tr('Пик {0:.2f} Гц, {1:.2f} dBFS (peak-ref). FFT {2}; разрешение {3:.4g} Гц; шаг графика {4:.4g} Гц; усреднено окон {5}. Показатели не эквивалентны оригинальной RMAA.').format(result['peak_frequency_hz'],result['peak_amplitude_dbfs'],result['fft_size'],result['resolution_hz'],result['frequency_spacing_hz'],result['window_count'])
        )

    def save_image(self):
        if self.current is None:
            messagebox.showinfo(tr('Спектр'), tr('Сначала проанализируйте WAV'), parent=self)
            return
        dest = filedialog.asksaveasfilename(
            title=tr('Сохранить график'), defaultextension=".png",
            filetypes=[("PNG", "*.png")]
        )
        if dest:
            self.fig.savefig(dest, dpi=160)
            self.status_var.set(tr('PNG сохранён'))


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    window = SpectrumViewer(root)
    window.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
