"""The same plot definitions are used by the desktop, comparisons and HTML."""

from rmaa_i18n import tr
from matplotlib.figure import Figure

PLOTS = {
    "acoustic_response": (tr('Акустическая АЧХ SAV'), tr('Частота, Гц'), "dB", True),
    "thd_frequency_curve": (tr('THD по частоте SAV'), tr('Частота, Гц'), "THD, dB", True),
    "imd_frequency": (tr('Полная IMD-кривая SAV'), tr('Центральная частота, Гц'), "IMD, dB", True),
    "response": (tr('Частотная характеристика'), tr('Частота, Гц'), tr('Относительно 1 кГц, dB'), True),
    "noise": (tr('Спектр шума'), tr('Частота, Гц'), "dBFS peak/bin", True),
    "dynamic_range": (tr('Спектр динамического диапазона'), tr('Частота, Гц'), "dBFS peak/bin", True),
    "tone": (tr('Спектр гармонических искажений'), tr('Частота, Гц'), "dBFS peak/bin", True),
    "imd_smpte": (tr('Спектр IMD SMPTE'), tr('Частота, Гц'), "dBFS peak/bin", True),
    "imd_10k": (tr('Спектр IMD 10 кГц'), tr('Частота, Гц'), "dBFS peak/bin", True),
    "imd_ccif": (tr('Спектр IMD CCIF'), tr('Частота, Гц'), "dBFS peak/bin", True),
    "imd_sweep": (tr('IMD по частоте'), tr('Верхняя частота, Гц'), "IMD, %", True),
    "crosstalk": (tr('Взаимопроникновение каналов'), tr('Частота, Гц'), tr('Утечка относительно основного канала, dB'), True),
    "phase": (tr('Фазовая характеристика'), tr('Частота, Гц'), tr('Фаза после компенсации задержки, °'), True),
    "impulse": (tr('Импульсная характеристика'), tr('Время относительно задержки, мс'), tr('Амплитуда'), False),
    "coherence": (tr('Когерентность'), tr('Частота, Гц'), tr('Когерентность'), True),
    "thd_frequency": (tr('Акустика: THD по частоте'), tr('Частота, Гц'), "THD, %", True),
    "thd_level": (tr('Акустика: THD по уровню'), tr('Уровень генератора, dBFS'), "THD, %", False),
}


def curves(result, kind):
    for side, letter in (("left", "L"), ("right", "R")):
        data = result.get(side, {})
        if kind in ("acoustic_response","thd_frequency_curve","imd_frequency"):
            section=data.get(kind,{})
            x,y=section.get("frequency_hz",[]),section.get("value_db",[])
        elif kind == "response":
            section = data.get(kind, {})
            x, y = section.get("frequency_hz", []), section.get("response_db", [])
        elif kind in ("noise", "dynamic_range", "tone", "imd_smpte", "imd_ccif", "imd_10k"):
            section = data.get(kind, {}).get("spectrum", {})
            x, y = section.get("frequency_hz", []), section.get("amplitude_dbfs_peak", [])
        elif kind == "imd_sweep":
            rows = data.get(kind, [])
            x, y = [r["frequency_hz"] for r in rows], [r.get("imd_pct",r.get("imd_noise_pct")) for r in rows]
        elif kind == "crosstalk":
            section = result.get("crosstalk_L_to_R" if side == "left" else "crosstalk_R_to_L", {})
            rows = section.get("curve", [section] if "frequency_hz" in section else [])
            x, y = [r["frequency_hz"] for r in rows], [r["crosstalk_db"] for r in rows]
            letter = "L→R" if side == "left" else "R→L"
        elif kind in ("phase", "coherence", "impulse"):
            section = data.get("phase", {})
            if kind == "impulse":
                x, y = section.get("impulse_time_ms", []), section.get("impulse_amplitude", [])
            else:
                section = section or data.get("response", {})
                x = section.get("frequency_hz", [])
                y = section.get("transfer_phase_deg", section.get("phase_deg", [])) if kind == "phase" else section.get("coherence", [])
        elif kind in ("thd_frequency", "thd_level"):
            rows = data.get(kind, [])
            x = [r["frequency_hz" if kind == "thd_frequency" else "nominal_level_dbfs"] for r in rows]
            y = [r["thd_pct"] for r in rows]
        else:
            raise ValueError("Unknown plot")
        if len(x) and len(x) == len(y):
            yield letter, x, y


def available_plots(results):
    return [key for key in PLOTS if any(list(curves(data, key)) or
            (key=='response' and any(data.get(side,{}).get('response',{}).get('valid') is False
                                    for side in ('left','right'))) for _, data in results)]


TECHNICAL_COLORS=('#b4f534','#42d9ff','#ff74d4','#ffb545','#9d99ff','#f7ed79','#4de2b3','#ff6868')


def set_response_limits(ax):
    """Keep flat FR readable at +/-0.5 dB; expand for larger real deviations."""
    import numpy as np
    values=[abs(float(y)) for line in ax.lines for y in line.get_ydata() if y is not None and np.isfinite(y)]
    span=max(.5,np.ceil(max(values,default=0.)*1.2*2)/2)
    ax.set_ylim(-span,span)
    from matplotlib.ticker import MultipleLocator,AutoLocator
    ax.yaxis.set_major_locator(MultipleLocator(.5) if span<=100 else AutoLocator())


def draw_plot(ax,results,kind,colors=None,technical=False,channels=('L','R'),visible=None):
    """Draw into the viewer's existing axes; report rendering stays independent."""
    from matplotlib.ticker import FixedLocator,FuncFormatter,LogLocator,MultipleLocator,AutoMinorLocator
    title, xlabel, ylabel, logarithmic = PLOTS[kind]
    ax.clear()
    for i, (label, data) in enumerate(results):
        if visible is not None and i not in visible:continue
        for j, (suffix, x, y) in enumerate(curves(data, kind)):
            if suffix[0] not in channels:continue
            kw = dict(label=f"{label} {suffix}", linewidth=1.1,
                      linestyle="-" if suffix.startswith('L') else "--")
            if technical:
                kw.update(color=TECHNICAL_COLORS[i%len(TECHNICAL_COLORS)],linewidth=1.15)
            if colors:
                kw["color"] = colors[i % len(colors)]
            ax.plot(x, y, **kw)
    if logarithmic:
        ax.set_xscale("log")
        positive=[float(v) for line in ax.lines for v in line.get_xdata() if v>0]
        if positive and min(positive)<max(positive):ax.set_xlim(min(positive),max(positive))
        elif technical:ax.set_xlim(20,20000)
    if kind == "coherence":
        ax.set_ylim(0, 1.05)
    if kind in ('response','acoustic_response'):
        set_response_limits(ax)
    ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
    if technical:
        ax.figure.set_facecolor('#141c18')
        ax.set_facecolor('#08110b')
        for spine in ax.spines.values():spine.set_color('#75867b')
        ax.tick_params(colors='#d8e4dc',labelsize=9)
        for text in (ax.xaxis.label,ax.yaxis.label,ax.title):text.set_color('#d8e4dc')
        ax.title.set_fontsize(11)
        ax.grid(True,which='major',color='#49634e',linewidth=.6,alpha=.85)
        ax.grid(True,which='minor',color='#263b2c',linewidth=.45,alpha=.85)
        if logarithmic:
            ticks=[10,20,30,50,100,200,300,500,1000,2000,3000,5000,10000,15000,20000,30000,50000]
            lo,hi=ax.get_xlim()
            ax.xaxis.set_major_locator(FixedLocator([f for f in ticks if lo<=f<=hi]))
            ax.xaxis.set_major_formatter(FuncFormatter(lambda value,pos:f'{value/1000:g}k' if value>=1000 else f'{value:g}'))
            ax.xaxis.set_minor_locator(LogLocator(base=10,subs=(2,3,4,5,6,7,8,9)))
            if kind in ('noise','tone','dynamic_range','imd_smpte','imd_ccif','imd_10k'):
                ax.set_xlim(20,min(22000,max(hi,20.01)))
        if kind in ('noise','tone','dynamic_range','imd_smpte','imd_ccif','imd_10k'):
            ax.set_ylim(-160,0)
            ax.yaxis.set_major_locator(MultipleLocator(10))
            ax.yaxis.set_minor_locator(MultipleLocator(5))
        elif kind=='response':
            ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value,pos:f'{value:g}'))
    else:
        ax.grid(True, which="both", alpha=.25)
    if ax.lines:
        legend=ax.legend(fontsize=8,loc='best',facecolor='#14231a' if technical else 'white',framealpha=.9)
        if technical:
            for text in legend.get_texts():text.set_color('#e4eee6')


def plot_figure(results, kind, colors=None):
    fig = Figure(figsize=(10, 5), dpi=100)
    ax = fig.add_subplot(111)
    draw_plot(ax,results,kind,colors=colors)
    fig.tight_layout()
    return fig
