"""8-slot results browser and report comparison (independent RMAA-inspired UI)."""
from __future__ import annotations

from rmaa_i18n import tr
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path
from rmaa_reports import load_result_json, report_html, report_txt, summary_rows


class ResultsBrowser(tk.Toplevel):
    def __init__(self, master, current=None):
        super().__init__(master)
        self.title(tr('RMAA Open Lab — результаты и сравнение (8 слотов)'))
        self.geometry("1030x640")
        self.slots = getattr(master,"results_slots",None) or [None]*8
        master.results_slots=self.slots
        self._make_ui()
        if current is not None:
            self.add(current, tr('Текущие измерения'))

    def _make_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text=tr('Выберите до 8 наборов результатов. Для сравнения выделите несколько слотов.')).pack(anchor="w")
        row = ttk.Frame(root)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text=tr('Открыть JSON / SAV…'), command=self.open_json).pack(side="left")
        ttk.Button(row, text=tr('Удалить выбранное'), command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(row, text=tr('HTML-отчёт…'), command=self.save_html).pack(side="left", padx=6)
        ttk.Button(row, text=tr('Экспорт TXT…'), command=self.save_txt).pack(side="left", padx=6)
        ttk.Button(row, text=tr('Сравнить числа / графики'), command=self.compare).pack(side="left", padx=6)
        sessionbar=ttk.Frame(root)
        sessionbar.pack(fill="x")
        ttk.Button(sessionbar,text=tr('Сохранить 8 слотов…'),command=self.save_session).pack(side="left")
        ttk.Button(sessionbar,text=tr('Открыть 8 слотов…'),command=self.load_session).pack(side="left",padx=6)
        ttk.Button(sessionbar,text=tr('Переименовать…'),command=self.rename_slot).pack(side="left",padx=6)
        ttk.Button(sessionbar,text=tr('Экспорт SAV…'),command=self.export_sav).pack(side="left",padx=6)
        cols = ("title","fs","level","thd","noise")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="extended")
        for name, txt, width in [("title",tr('Набор результатов'),300),("fs",tr('Fs, Гц'),100),
                                 ("level",tr('Тестовый уровень'),120),("thd","THD L, %",110),
                                 ("noise",tr('Шум L, dBFS'),120)]:
            self.tree.heading(name, text=txt)
            self.tree.column(name, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        for i in range(8):
            self.tree.insert("", "end", iid=str(i), values=(tr('Слот {0}: пусто').format(i + 1), "", "", "", ""))
        ttk.Label(root, text=tr('SAV 6.4.5 F9: числа и графики; для дополнительных данных Open Lab используйте JSON. Сравнение не означает точного совпадения методики.'),
                  wraplength=960).pack(anchor="w", pady=7)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        menu=tk.Menu(self,tearoff=False)
        menu.add_command(label=tr('Числа / графики'),command=self.compare)
        menu.add_command(label=tr('Переименовать…'),command=self.rename_slot)
        menu.add_command(label=tr('Экспорт SAV…'),command=self.export_sav)
        menu.add_command(label=tr('HTML-отчёт…'),command=self.save_html)
        def context(event):
            item=self.tree.identify_row(event.y)
            if item:
                if item not in self.tree.selection():self.tree.selection_set(item)
                menu.tk_popup(event.x_root,event.y_root)
        self.tree.bind("<Button-3>",context)
        detailframe = ttk.LabelFrame(root, text=tr('Детализация выделенного измерения'))
        detailframe.pack(fill="both", pady=6)
        self.details = tk.Text(detailframe, height=10, wrap="none", font=("Consolas", 10))
        self.details.pack(fill="both", expand=True)
        self.show_detail()

    def _refresh(self):
        for idx, item in enumerate(self.slots):
            if item is None:
                values = (tr('Слот {0}: пусто').format(idx + 1), "", "", "", "")
            else:
                name, data = item
                try:
                    thd = "%.5f" % data["left"]["tone"]["thd_pct"]
                    noise = "%.1f" % data["left"]["noise"]["noise_dbfs_rms"]
                except KeyError:
                    thd, noise = "—", "—"
                values = (name, data.get("fs", "?"), data.get("level_dbfs", "?"), thd, noise)
            self.tree.item(str(idx), values=values)
        self.show_detail()

    def add(self, data, label):
        if not isinstance(data, dict) or "left" not in data or "right" not in data:
            raise ValueError(tr('Некорректные данные результатов'))
        for index, item in enumerate(self.slots):
            if item is None:
                self.slots[index] = (str(label), data)
                self._refresh()
                self.tree.selection_set(str(index))
                return
        raise ValueError(tr('Заняты все 8 слотов; удалите один из наборов'))

    def open_json(self):
        paths = filedialog.askopenfilenames(
            parent=self, title=tr('Открыть результаты RMAA Open Lab'),
            filetypes=[(tr('Результаты'), "*.json *.sav"),("JSON","*.json"),("RMAA SAV","*.sav")])
        for path in paths:
            try:
                self.add(load_result_json(path), Path(path).stem)
            except Exception as exc:
                messagebox.showerror(tr('Ошибка загрузки'), f"{Path(path).name}\n{exc}", parent=self)

    def remove_selected(self):
        for str_index in self.tree.selection():
            self.slots[int(str_index)] = None
        self._refresh()

    def selected(self):
        chosen = [
            self.slots[int(i)] for i in self.tree.selection()
            if self.slots[int(i)] is not None
        ]
        if not chosen:
            messagebox.showinfo(tr('Результаты'), tr('Выделите хотя бы один заполненный слот.'), parent=self)
        return chosen

    def show_detail(self, event=None):
        if not hasattr(self, "details"):
            return
        chosen = [
            self.slots[int(i)] for i in self.tree.selection()
            if self.slots[int(i)] is not None
        ]
        self.details.delete("1.0", "end")
        if len(chosen) != 1:
            self.details.insert("end", tr('Выберите один заполненный слот для детализации.'))
            return
        label, data = chosen[0]
        self.details.insert("end", label + "\n\n")
        for title, l, r in summary_rows(data):
            self.details.insert("end", f"{title:47s} L:{l:>12s}  R:{r:>12s}\n")

    def save_html(self):
        chosen = self.selected()
        if not chosen:
            return
        target = filedialog.asksaveasfilename(
            parent=self, title=tr('HTML-отчёт'), defaultextension=".html",
            filetypes=[("HTML", "*.html")])
        if target:
            try:
                Path(target).write_text(report_html(chosen), encoding="utf-8")
                messagebox.showinfo(tr('Сохранено'), target, parent=self)
            except Exception as exc:
                messagebox.showerror(tr('HTML-отчёт'), str(exc), parent=self)

    def save_txt(self):
        chosen = self.selected()
        if not chosen:
            return
        target = filedialog.asksaveasfilename(
            parent=self, title=tr('Текстовый отчёт'), defaultextension=".txt",
            filetypes=[("TXT", "*.txt")])
        if target:
            try:
                Path(target).write_text(report_txt(chosen), encoding="utf-8")
            except Exception as exc:
                messagebox.showerror(tr('Текстовый отчёт'), str(exc), parent=self)

    def compare(self):
        chosen=self.selected()
        if chosen:
            from rmaa_result_window import ResultWindow
            ResultWindow(self,chosen)

    def export_sav(self):
        chosen=self.selected()
        if len(chosen)!=1:
            messagebox.showinfo("SAV",tr('Выберите один результат для экспорта в один SAV.'),parent=self)
            return
        target=filedialog.asksaveasfilename(parent=self,defaultextension=".sav",filetypes=[("RMAA SAV","*.sav")])
        if not target:return
        try:
            from rmaa_sav import save_sav
            save_sav(target,chosen[0][1],label=chosen[0][0])
            messagebox.showinfo("SAV",tr('Сохранены данные, представимые в RMAA F9. Собственные phase/impulse, CCIF 19/20 kHz и THD(level) сохраняйте в JSON.'),parent=self)
        except Exception as exc:messagebox.showerror("SAV",str(exc),parent=self)

    def rename_slot(self):
        selection=self.tree.selection()
        if len(selection)!=1:return
        index=int(selection[0])
        if self.slots[index] is None:return
        label=simpledialog.askstring(tr('Название'),tr('Название измерения:'),initialvalue=self.slots[index][0],parent=self)
        if label:
            self.slots[index]=(label,self.slots[index][1])
            self._refresh()

    def save_session(self):
        import json
        path=filedialog.asksaveasfilename(parent=self,defaultextension=".json",initialfile="rmaa-session.json")
        if path:
            Path(path).write_text(json.dumps({"type":"rmaa-slots","version":1,"slots":self.slots},ensure_ascii=False),encoding="utf-8")

    def load_session(self):
        import json
        path=filedialog.askopenfilename(parent=self,filetypes=[("JSON","*.json")])
        if not path:return
        try:
            if Path(path).stat().st_size>512_000_000:raise ValueError("Session exceeds 512 MB")
            data=json.loads(Path(path).read_text(encoding="utf-8"))
            slots=data.get("slots")
            if data.get("type")!="rmaa-slots" or data.get("version")!=1 or not isinstance(slots,list) or len(slots)!=8:
                raise ValueError("Invalid saved session")
            for slot in slots:
                if slot is not None and (not isinstance(slot,list) or len(slot)!=2 or not isinstance(slot[0],str) or not isinstance(slot[1],dict) or not all(k in slot[1] for k in ("left","right"))):
                    raise ValueError("Invalid result slot")
            self.slots[:]=slots
            self._refresh()
        except Exception as exc:
            messagebox.showerror(tr('Сеанс'),str(exc),parent=self)

    def _save_chart(self, fig):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".png",
            filetypes=[("PNG", "*.png")])
        if path:
            fig.savefig(path, dpi=150)
