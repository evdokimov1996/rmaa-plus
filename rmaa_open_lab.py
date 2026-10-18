"""RMAA Open Lab: an independent experimental audio analyzer."""
from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from audio_core import (
    analyze_noise, analyze_tone, check_audio_devices,
    generate_sweep, generate_tone, run_suite, save_json
)

BG = "#161b23"
PANEL = "#222936"
FG = "#edf2fa"
MUTED = "#a8b6c9"


class AnalyzerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RMAA Open Lab — экспериментальный аудиоанализатор")
        self.geometry("1100x770")
        self.minsize(820, 590)
        self.configure(bg=BG)
        self.events = queue.Queue()
        self.results = None
        self.inputs, self.outputs = [], []
        self.configure_style()
        self.draw_controls()
        self.after(150, self.drain_events)
        self.refresh_devices()

    def configure_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Header.TLabel", font=("Segoe UI Semibold", 19))
        s.configure("Info.TLabel", foreground=MUTED)
        s.configure("TButton", background=PANEL, foreground=FG, padding=7)
        s.map("TButton", background=[("active", "#36567c")])
        s.configure("TCombobox", fieldbackground=PANEL, foreground=FG)
        s.configure("TLabelFrame", background=BG, foreground=FG)
        s.configure("TLabelFrame.Label", background=BG, foreground=FG)

    def draw_controls(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=18, pady=(15, 9))
        ttk.Label(header, text="RMAA Open Lab", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header, style="Info.TLabel",
            text="Собственная реализация • спектральные измерения • отчёты JSON • прототип, не замена калиброванному RMAA",
        ).pack(anchor="w", pady=(2, 0))

        ctl = ttk.LabelFrame(self, text="Аудиотракт / loopback", padding=11)
        ctl.pack(fill="x", padx=18)
        for i in range(4):
            ctl.columnconfigure(i, weight=1 if i in (1, 3) else 0)
        ttk.Label(ctl, text="Вход (Line In):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.input_sel = ttk.Combobox(ctl, state="readonly", width=43)
        self.input_sel.grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Label(ctl, text="Выход (Line Out):").grid(row=0, column=2, sticky="w", padx=5)
        self.output_sel = ttk.Combobox(ctl, state="readonly", width=43)
        self.output_sel.grid(row=0, column=3, sticky="ew", padx=5)

        ttk.Label(ctl, text="Дискретизация:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.fs_sel = ttk.Combobox(ctl, state="readonly", values=["44100", "48000", "96000"], width=13)
        self.fs_sel.set("48000")
        self.fs_sel.grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(ctl, text="Уровень, dBFS:").grid(row=1, column=2, sticky="w", padx=5)
        self.level_sel = ttk.Combobox(ctl, state="readonly", values=["-24", "-18", "-12", "-6"], width=13)
        self.level_sel.set("-18")
        self.level_sel.grid(row=1, column=3, sticky="w", padx=5)
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=18, pady=10)
        self.run_btn = ttk.Button(actions, text="▶ Измерить", command=self.run_test)
        self.run_btn.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="↻ Обновить устройства", command=self.refresh_devices).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Сохранить отчёт", command=self.save_results).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Создать тестовые WAV", command=self.export_signals).pack(side="left")

        self.message = tk.StringVar(value="Выберите вход и выход. Подключите Line Out → Line In.")
        ttk.Label(self, textvariable=self.message, style="Info.TLabel").pack(fill="x", padx=21)
        self.figure = Figure(figsize=(10, 4.1), dpi=100, facecolor=BG)
        self.plot = self.figure.add_subplot(111)
        self.plot.set_facecolor(PANEL)
        self.plot.set_title("Частотная характеристика (после измерения)", color=FG)
        self.plot.set_xscale("log")
        self.plot.set_xlim(20, 20000)
        self.plot.set_ylim(-6, 6)
        self.plot.grid(True, which="both", alpha=0.22)
        self.plot.tick_params(colors=FG)
        for spine in self.plot.spines.values():
            spine.set_color(MUTED)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=18, pady=5)
        self.figure.tight_layout(pad=1.6)

        txtframe = ttk.LabelFrame(self, text="Измерения и ограничения", padding=4)
        txtframe.pack(fill="both", padx=18, pady=(0, 15))
        self.report = tk.Text(
            txtframe, height=7, wrap="word", bg=PANEL, fg=FG,
            insertbackground=FG, relief="flat", font=("Consolas", 10)
        )
        self.report.pack(fill="both", expand=True)
        self.print_report(
            "Это независимый прототип. Нужен физический кабель Line Out → Line In.\n"
            "Не соединяйте выход усилителя с микрофонным входом. Начальный сигнал −18 dBFS.\n"
            "Без калибровки значения не сопоставимы непосредственно с RMAA."
        )

    def print_report(self, text):
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)

    def refresh_devices(self):
        try:
            self.inputs, self.outputs = check_audio_devices()
            self.input_sel["values"] = [f'{d["id"]}: {d["name"]}' for d in self.inputs]
            self.output_sel["values"] = [f'{d["id"]}: {d["name"]}' for d in self.outputs]
            if self.inputs:
                self.input_sel.current(0)
            if self.outputs:
                self.output_sel.current(0)
            self.message.set(f"Входов: {len(self.inputs)}; выходов: {len(self.outputs)}. Убедитесь в физическом loopback.")
        except Exception as err:
            self.message.set(f"Не удалось прочитать аудиоустройства: {err}")

    def run_test(self):
        try:
            input_id = self.inputs[self.input_sel.current()]["id"]
            output_id = self.outputs[self.output_sel.current()]["id"]
            fs = int(self.fs_sel.get())
            level = float(self.level_sel.get())
        except (ValueError, IndexError):
            messagebox.showerror("Выбор устройства", "Выберите вход, выход, частоту и уровень.")
            return
        if not messagebox.askyesno(
            "Проверка подключения",
            "Кабель Line Out → Line In подключён? Убедитесь, что вход не микрофонный "
            "и выход не усиленный. Тест включает короткие звуковые сигналы."
        ):
            return
        self.run_btn.configure(state="disabled")
        self.message.set("Идёт измерение: синус, уровень шума и частотная характеристика…")
        def work():
            try:
                value = run_suite(input_id, output_id, fs, level)
                self.events.put(("result", value))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.run_btn.configure(state="normal")
                if kind == "error":
                    self.message.set("Ошибка измерения")
                    messagebox.showerror("Аудиотракт", value)
                else:
                    self.results = value
                    self.present(value)
        except queue.Empty:
            pass
        self.after(150, self.drain_events)

    def present(self, result):
        self.plot.clear()
        self.plot.set_facecolor(PANEL)
        self.plot.set_xscale("log")
        self.plot.set_xlim(20, min(20000, result["fs"] * 0.45))
        self.plot.set_title("АЧХ относительно 1 кГц", color=FG)
        self.plot.set_ylabel("dB", color=FG)
        self.plot.set_xlabel("Hz", color=FG)
        self.plot.tick_params(colors=FG)
        self.plot.grid(True, which="both", alpha=0.25)
        summary = []
        for side, name, color in [("left", "Левый", "#4cc9f0"), ("right", "Правый", "#f72585")]:
            data = result[side]
            response = data["response"]
            self.plot.semilogx(response["frequency_hz"], response["response_db"], label=name, color=color)
            tone = data["tone"]
            noise = data["noise"]
            summary.append(
                f'{name}: АЧХ {response["min_db"]:+.2f} … {response["max_db"]:+.2f} dB; '
                f'Шум {noise["noise_dbfs_rms"]:.1f} dBFS; '
                f'THD {tone["thd_pct"]:.5f} %; THD+N {tone["thdn_pct"]:.4f} % '
                f'({tone["thdn_db"]:.1f} dB); задержка {response["latency_ms"]:.1f} мс'
            )
        self.plot.legend(facecolor=PANEL, labelcolor=FG)
        self.canvas.draw_idle()
        summary.append("")
        summary.extend(result["limitations"])
        self.print_report("\n".join(summary))
        self.message.set("Измерения завершены: экспериментальные значения, не откалиброваны.")

    def save_results(self):
        if self.results is None:
            messagebox.showinfo("Нет данных", "Сначала проведите измерение.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить отчёт", defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="audio-measurement-" + datetime.now().strftime("%Y%m%d-%H%M") + ".json"
        )
        if path:
            save_json(path, self.results)
            self.message.set("Отчёт сохранён: " + path)

    def export_signals(self):
        import soundfile as sf
        directory = filedialog.askdirectory(title="Папка для тестовых WAV")
        if not directory:
            return
        fs = int(self.fs_sel.get())
        level = float(self.level_sel.get())
        sf.write(str(Path(directory) / "tone_997Hz.wav"), generate_tone(fs, seconds=3, level_dbfs=level), fs, subtype="PCM_24")
        sf.write(str(Path(directory) / "sweep_20Hz_20kHz.wav"), generate_sweep(fs, seconds=5, level_dbfs=level), fs, subtype="PCM_24")
        self.message.set("Созданы 24-битные WAV с тестовыми сигналами в " + directory)


if __name__ == "__main__":
    AnalyzerApp().mainloop()
