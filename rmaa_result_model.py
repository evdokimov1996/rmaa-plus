"""Presentation-only rows and channel selection; never modifies measurements."""
import math
from rmaa_i18n import tr
from rmaa_reports import summary_rows


NAV_NAMES = {
    'response': 'АЧХ', 'acoustic_response': 'Акустическая АЧХ SAV',
    'noise': 'Уровень шума', 'dynamic_range': 'Динамический диапазон',
    'tone': 'Гармоники / THD+N', 'imd_smpte': 'IMD SMPTE',
    'imd_10k': 'IMD 10 кГц', 'imd_ccif': 'IMD CCIF 19/20 кГц',
    'imd_sweep': 'IMD по частоте', 'imd_frequency': 'Полная IMD-кривая SAV',
    'crosstalk': 'Взаимопроникновение каналов', 'phase': 'Фаза',
    'impulse': 'Импульсная характеристика', 'coherence': 'Когерентность',
    'thd_frequency': 'THD от частоты', 'thd_level': 'THD от уровня',
    'thd_frequency_curve': 'THD по частоте SAV',
}
ORDER=tuple(NAV_NAMES)

# Label, dictionary section/key, display format. Units live in labels, so values
# remain easy to compare in aligned columns instead of repeated prose.
FIELDS={
 'response': [('АЧХ 20–20000 Гц: минимум, dB','response','min_20_20000_db','+.3f'),
              ('АЧХ 20–20000 Гц: максимум, dB','response','max_20_20000_db','+.3f'),
              ('АЧХ 40–15000 Гц: минимум, dB','response','min_db','+.3f'),
              ('АЧХ 40–15000 Гц: максимум, dB','response','max_db','+.3f')],
 'noise': [('Шум RMS, dBFS','noise','noise_dbfs_rms','.2f'),
           ('Шум A-weighted, dBFS','noise','noise_a_dbfs_rms','.2f'),
           ('Шум относительно генератора, dB','noise','normalized_noise_dbfs_rms','.2f'),
           ('Шум A относительно генератора, dB','noise','normalized_noise_a_dbfs_rms','.2f'),
           ('Пиковый уровень, dBFS','noise','peak_dbfs','.2f'),
           ('DC offset, доля FS','noise','dc_offset','.6g')],
 'dynamic_range': [('Динамический диапазон, dB','dynamic_range','dynamic_range_db','.2f'),
                   ('Динамический диапазон A-weighted, dB','dynamic_range','dynamic_range_a_db','.2f'),
                   ('Уровень пилота RMS, dBFS','dynamic_range','pilot_dbfs_rms','.2f'),
                   ('Остаточный шум RMS, dBFS','dynamic_range','residual_noise_dbfs_rms','.2f')],
 'tone': [('THD, %','tone','thd_pct','.5f'),('THD+N, %','tone','thdn_pct','.5f'),
          ('THD+N, dB','tone','thdn_db','.2f'),('THD+N A-weighted, dB','tone','thdn_a_db','.2f'),
          ('Основной тон, dBFS peak','tone','fundamental_dbfs_peak','.2f'),
          ('Измеренная частота, Гц','tone','measured_frequency_hz','.3f')],
 'phase': [('Задержка сеанса, мс','phase','session_latency_ms','.3f'),
           ('Остаточная задержка, мс','phase','latency_ms','.3f')],
}
for kind in ('imd_smpte','imd_10k','imd_ccif'):
    FIELDS[kind]=[('IMD, %',kind,'imd_pct','.5f'),('IMD + шум, %',kind,'imd_noise_pct','.5f'),
                  ('IMD + шум A-weighted, %',kind,'imd_noise_a_pct','.5f')]


def display_number(value,fmt='.3f'):
    try:
        value=float(value)
        if not math.isfinite(value):return '—'
        result=format(value,fmt)
        # Never round a measured nonzero value to a misleading zero.
        if value and float(result)==0:return format(value,'.3g')
        return result
    except (TypeError,ValueError,OverflowError):return '—'


def result_columns(results,channels=('L','R')):
    return [(i,side,letter,f'{name} · {letter}') for i,(name,data) in enumerate(results)
            for side,letter in (('left','L'),('right','R')) if letter in channels and data.get(side)]


def measurement_rows(results,kind,channels=('L','R')):
    columns=result_columns(results,channels)
    def row(label,get,fmt):
        values=[display_number(get(results[i][1],side),fmt) for i,side,_,_ in columns]
        return (tr(label),values)
    rows=[]
    for label,section,key,fmt in FIELDS.get('phase' if kind in ('impulse','coherence') else kind,[]):
        rows.append(row(label,lambda data,side,s=section,k=key:data.get(side,{}).get(s,{}).get(k),fmt))
    if kind=='crosstalk':
        for hz in (1000,):
            def get(data,side,f=hz):
                value=data.get('crosstalk_L_to_R' if side=='left' else 'crosstalk_R_to_L',{})
                return value.get('crosstalk_db') if f==1000 else None
            rows.append(row(tr('Crosstalk при {0} Гц, dB').format(hz),get,'.2f'))
    if kind=='imd_sweep':
        frequencies=sorted({point['frequency_hz'] for _,data in results for side in ('left','right') for point in data.get(side,{}).get('imd_sweep',[])})
        for hz in frequencies:
            rows.append(row(tr('IMD + шум при {0:g} Гц, %').format(hz),
                lambda data,side,f=hz:next((p.get('imd_noise_pct') for p in data.get(side,{}).get('imd_sweep',[]) if p['frequency_hz']==f),None),'.5f'))
    return columns,[(label,values) for label,values in rows if any(v!='—' for v in values)]


def overview_rows(results,channels=('L','R')):
    columns=result_columns(results,channels)
    maps=[{label:(left,right) for label,left,right in summary_rows(data)} for _,data in results]
    labels=dict.fromkeys(label for mapping in maps for label in mapping)
    rows=[]
    for label in labels:
        values=[maps[i].get(label,('—','—'))[0 if side=='left' else 1] for i,side,_,_ in columns]
        if any(v!='—' for v in values):rows.append((label,values))
    return columns,rows


def calibration_rows(results):
    rows=[]
    fields=[('Частота дискретизации, Гц',lambda d:d.get('fs'),'.0f'),
            ('Шаг частоты АЧХ, Гц',lambda d:d.get('left',{}).get('response',{}).get('frequency_spacing_hz'),'.2f'),
            ('АЧХ: усреднённых периодов',lambda d:d.get('left',{}).get('response',{}).get('averaged_periods'),'.0f'),
            ('АЧХ: остаточный дрейф, ppm',lambda d:d.get('left',{}).get('response',{}).get('clock_residual_ppm'),'+.5f'),
            ('АЧХ: макс. дрейф периода, ppm',lambda d:d.get('left',{}).get('response',{}).get('clock_max_step_ppm'),'.5f'),
            ('АЧХ: контроль полупериодов, ppm',lambda d:d.get('left',{}).get('response',{}).get('period_clock_tracking',{}).get('final_half_period_max_step_ppm'),'.5f'),
            ('АЧХ: случайная ошибка опоры 1 кГц, дБ',lambda d:d.get('left',{}).get('response',{}).get('reference_random_noise_standard_error_db'),'.5f'),
            ('АЧХ: точек с ограничением достоверности',lambda d:d.get('left',{}).get('response',{}).get('uncertainty_limited_bins'),'.0f'),
            ('Пик записи, dBFS',lambda d:d.get('calibration',{}).get('peak_dbfs'),'.2f'),
            ('Сходство маркера',lambda d:d.get('marker_coherence',d.get('marker_similarity')),'.5f'),
            ('Дрейф тактов, ppm',lambda d:d.get('calibration',{}).get('clock_drift_ppm'),'+.3f'),
            ('Остаточный дрейф, ppm',lambda d:d.get('calibration',{}).get('clock_residual_ppm'),'+.5f'),
            ('Усиление калибровочного тона, dB',lambda d:d.get('calibration',{}).get('calibration_tone_gain_db'),'+.3f')]
    for label,get,fmt in fields:
        values=[display_number(get(data),fmt) for _,data in results]
        if any(v!='—' for v in values):rows.append((tr(label),values))
    for label,get in [('Аудио API',lambda d:d.get('api_mode')),('Формат потока',lambda d:d.get('pcm_format')),
                      ('Фактический host API',lambda d:d.get('actual_host_api')),
                      ('Каналы',lambda d:tr('Моно') if not d.get('right') else 'L / R')]:
        rows.append((tr(label),[str(get(data) or '—') for _,data in results]))
    return rows
