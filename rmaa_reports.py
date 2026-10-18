"""Self-contained HTML/TXT report writer; NEVER labels results as original RMAA."""
from __future__ import annotations

from rmaa_i18n import tr

import base64
import html
import io
import json
from datetime import datetime, timezone

def summary_rows(result):
    columns = []
    fields = [
        (tr('АЧХ 40 Hz–15 kHz: min, dB'), ("response","min_db"), "{:+.2f}"),
        (tr('АЧХ 40 Hz–15 kHz: max, dB'), ("response","max_db"), "{:+.2f}"),
        (tr('Шум, dBFS RMS (без А-взвешивания)'), ("noise","noise_dbfs_rms"), "{:.2f}"),
        (tr('Шум A-weighted, dBFS RMS'), ("noise","noise_a_dbfs_rms"), "{:.2f}"),
        (tr('Шум относительно 0 dBFS генератора, dB'),("noise","normalized_noise_dbfs_rms"),"{:.2f}"),
        (tr('Шум относительно генератора A-weighted, dB'),("noise","normalized_noise_a_dbfs_rms"),"{:.2f}"),
        (tr('Пиковый уровень, dBFS'), ("noise","peak_dbfs"), "{:.2f}"),
        (tr('Динамический диапазон, dB'), ("dynamic_range","dynamic_range_db"), "{:.2f}"),
        (tr('Динамический диапазон A-weighted, dB'), ("dynamic_range","dynamic_range_a_db"), "{:.2f}"),
        ("THD, %", ("tone","thd_pct"), "{:.5f}"),
        ("THD+N, %", ("tone","thdn_pct"), "{:.5f}"),
        ("THD+N, dB", ("tone","thdn_db"), "{:.2f}"),
        ("THD+N A-weighted, dB", ("tone","thdn_a_db"), "{:.2f}"),
        ("IMD SMPTE, %", ("imd_smpte","imd_pct"), "{:.5f}"),
        (tr('IMD + шум SMPTE, %'), ("imd_smpte","imd_noise_pct"), "{:.5f}"),
        ("IMD SMPTE + noise A-weighted, %", ("imd_smpte","imd_noise_a_pct"), "{:.5f}"),
        (tr('IMD 10 кГц, %'),("imd_10k","imd_pct"),"{:.5f}"),
        (tr('IMD + шум 10 кГц, %'),("imd_10k","imd_noise_pct"),"{:.5f}"),
        ("IMD CCIF, %", ("imd_ccif","imd_pct"), "{:.5f}"),
        (tr('IMD + шум CCIF, %'), ("imd_ccif","imd_noise_pct"), "{:.5f}"),
        (tr('Задержка, мс'), ("response","latency_ms"), "{:.2f}"),
    ]
    selection=result.get("selected_tests")
    sections={"response":"response","noise":"noise","dynamic_range":"dynamic_range",
              "tone":"thd","imd_smpte":"imd_smpte","imd_ccif":"imd_ccif","imd_10k":"imd_10k"}
    fields[0:0]=[
        (tr('АЧХ 20 Hz–20 kHz: min, dB'),("response","min_20_20000_db"),"{:+.2f}"),
        (tr('АЧХ 20 Hz–20 kHz: max, dB'),("response","max_20_20000_db"),"{:+.2f}")]
    for title, (section, key), fmt in fields:
        if selection is not None:
            flag="thdn" if section=="tone" and key.startswith("thdn") else sections[section]
            if flag not in selection:continue
        values = []
        for side in ("left", "right"):
            try:
                values.append(fmt.format(float(result[side][section][key])))
            except (KeyError, ValueError, TypeError):
                values.append("—")
        columns.append((title, *values))
    try:
        columns.append((tr('Взаимопроникновение, dB'),
                        "%.2f" % result["crosstalk_L_to_R"]["crosstalk_db"],
                        "%.2f" % result["crosstalk_R_to_L"]["crosstalk_db"]))
        columns.append((tr('Разность каналов R-L, dB'), "—",
                        "%.2f" % result["stereo_balance"]["right_minus_left_db"]))
    except (KeyError, TypeError, ValueError):
        pass
    from rmaa_quality import quality_rows
    return columns+quality_rows(result)


def _plot_png(results):
    from matplotlib.figure import Figure
    figure = Figure(figsize=(10, 4.5), dpi=120)
    ax = figure.add_subplot(111)
    plotted = False
    for i, (label, result) in enumerate(results):
        for side, suffix in (("left", "L"), ("right", "R")):
            try:
                response = result[side]["response"]
                ax.semilogx(response["frequency_hz"], response["response_db"],
                            label=f"{label} {suffix}", linewidth=1.3)
                plotted = True
            except KeyError:
                continue
    if not plotted:
        return None
    ax.set_title(tr('Относительная частотная характеристика — экспериментальная'))
    ax.set_xlabel(tr('Частота, Гц'))
    ax.set_ylabel(tr('Уровень относительно 1 кГц, dB'))
    ax.set_xlim(20, 20000)
    ax.grid(True, which="both", alpha=.23)
    ax.legend()
    figure.tight_layout()
    buf = io.BytesIO()
    figure.savefig(buf, format="png")
    return buf.getvalue()


def _imd_plot_png(results):
    from matplotlib.figure import Figure
    figure=Figure(figsize=(10,4.5),dpi=120)
    ax=figure.add_subplot(111)
    found=False
    for title,result in results:
        for side,suffix in (("left","L"),("right","R")):
            curve=result.get(side,{}).get("imd_sweep",[])
            if curve:
                ax.semilogx([r["frequency_hz"] for r in curve],
                            [r["imd_pct"] for r in curve],
                            marker=".",linewidth=1.5,label=f"{title} {suffix}")
                found=True
    if not found:
        return None
    ax.set_title(tr('Интермодуляционные искажения в зависимости от частоты'))
    ax.set_xlabel(tr('Верхняя частота, Гц'))
    ax.set_ylabel("IMD, %")
    ax.grid(True,which="both",alpha=.3)
    ax.legend()
    figure.tight_layout()
    buffer=io.BytesIO()
    figure.savefig(buffer,format="png")
    return buffer.getvalue()


def report_html(results):
    """results: list of (label, result dict); 1..8 datasets."""
    if not 1 <= len(results) <= 8:
        raise ValueError("Report requires 1 to 8 result datasets")
    esc = lambda x: html.escape(str(x), quote=True)
    sections = []
    for index, (label, result) in enumerate(results, 1):
        from rmaa_quality import response_warnings
        table = "".join(
            "<tr><th>" + esc(name) + "</th><td>" + esc(left) +
            "</td><td>" + esc(right) + "</td></tr>"
            for name, left, right in summary_rows(result)
        )
        settings = (
            tr('Fs: {0} Гц, генератор: {1} dBFS peak, устройство входа: {2}, устройство выхода: {3}.').format(esc(result.get('fs', '?')),esc(result.get('level_dbfs', '?')),esc(result.get('input_device', '?')),esc(result.get('output_device', '?')))
        )
        settings+=''.join('<br><strong style="color:#9b3100">'+esc(message)+'</strong>' for message in response_warnings(result))
        sections.append(
            tr('<section><h2>{0}. {1}</h2><p>{2}</p><table><thead><tr><th>Параметр</th><th>Левый канал</th><th>Правый канал</th></tr></thead><tbody>{3}</tbody></table></section>').format(index,esc(label),settings,table)
        )
    from rmaa_plots import available_plots,plot_figure,PLOTS
    # Aligned comparison table, including missing values for unmeasured tests.
    names=list(dict.fromkeys(row[0] for _,data in results for row in summary_rows(data)))
    datasets=[{row[0]:row[1:] for row in summary_rows(data)} for _,data in results]
    head="".join(f'<th>{esc(label)} L</th><th>{esc(label)} R</th>' for label,_ in results)
    body="".join('<tr><th>'+esc(name)+'</th>'+''.join(
        '<td>'+esc(v)+'</td>' for data in datasets for v in data.get(name,("—","—")))+'</tr>' for name in names)
    sections.insert(0,tr('<section><h2>Сравнение измерений</h2><div style="overflow:auto"><table><thead><tr><th>Параметр</th>')+head+'</tr></thead><tbody>'+body+'</tbody></table></div></section>')
    for kind in available_plots(results):
        figure=plot_figure(results,kind)
        buffer=io.BytesIO()
        figure.savefig(buffer,format="png",dpi=110)
        figure.clear()
        encoded=base64.b64encode(buffer.getvalue()).decode("ascii")
        title=esc(PLOTS[kind][0])
        sections.append(f'<section><h2>{title}</h2><img class="chart" alt="{title}" src="data:image/png;base64,{encoded}"></section>')
    stylesheet = """
    body{font-family:Segoe UI,Arial,sans-serif;color:#222;max-width:1150px;
    margin:35px auto;padding:0 24px;background:#fff}
    h1{font-size:24px}h2{font-size:19px}p{line-height:1.5}
    section{margin:30px 0}table{width:100%;border-collapse:collapse}
    th,td{border:1px solid #ced6dd;padding:8px;text-align:left}
    thead{background:#eaf0f5}tbody tr:nth-child(even){background:#f6f8fa}
    .warn{background:#fff6df;border-left:4px solid #a97c21;padding:12px}
    .chart{max-width:100%;height:auto}footer{margin:35px 0;color:#677;font-size:12px}
    @media print{body{margin:0}.warn{print-color-adjust:exact}.print{display:none}section{break-inside:avoid}}
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        tr('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>RMAA Open Lab — отчёт</title><style>')
        + stylesheet + tr('</style></head><body><h1>RMAA Open Lab — отчёт измерений</h1><button class="print" onclick="window.print()">Печать / сохранить PDF</button><p class="warn"><strong>Экспериментальная реализация.</strong> Не сертифицировано и не откалибровано; результаты НЕ являются численно эквивалентными RightMark Audio Analyzer 6.4.5. A-взвешивание рассчитано по аналоговой частотной характеристике на FFT bins. Невыполненные тесты не подменяются вымышленными значениями.</p>')
        + "".join(sections) +
        tr('<footer>Сформировано {0}. Независимое ПО.</footer></body></html>').format(timestamp)
    )


def report_txt(results):
    if not 1 <= len(results) <= 8:
        raise ValueError("Select 1 to 8 datasets")
    lines = [
        tr('RMAA Open Lab | Экспериментальные измерения (НЕ идентичны RMAA)'),
        "=" * 68
    ]
    for label, result in results:
        lines.append("\n" + str(label))
        lines.append("Fs: "+str(result.get("fs","?")))
        from rmaa_quality import response_warnings
        lines.extend(response_warnings(result))
        for name, left, right in summary_rows(result):
            lines.append(f"{name:43s}: L {left:>11s} | R {right:>11s}")
    return "\n".join(lines) + "\n"


def load_result_json(path):
    from pathlib import Path
    if Path(path).suffix.lower()==".sav":
        from rmaa_sav import load_sav
        return load_sav(path)
    from pathlib import Path
    if Path(path).stat().st_size > 256_000_000:
        raise ValueError("Result JSON exceeds 256 MB")
    with open(path, "r", encoding="utf-8-sig") as f:
        result = json.load(f)
    if not isinstance(result, dict) or "left" not in result or "right" not in result:
        raise ValueError("Not an RMAA Open Lab result JSON")
    return result
