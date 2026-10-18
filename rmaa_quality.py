"""Informational Open Lab ratings, never presented as RightMark ratings.

Thresholds are a transparent display convention, not device certification.
The overall value is the worst available metric; missing data are not graded.
"""
import math

LABELS=("Excellent","Very good","Good","Average","Poor")
# Four inclusive upper bounds; larger deviations receive the next category.
LIMITS={"FR dB":(.1,.5,1.,3.),"Noise dB":(-100.,-90.,-80.,-70.),
        "Dynamic range dB":(-100.,-90.,-80.,-70.),"THD %":(.001,.005,.01,.05),
        "IMD + noise %":(.005,.01,.05,.1)}


def quality_rows(result):
    scores={};rows=[]
    for side in ('left','right'):
        data=result.get(side,{})
        if not data:continue
        values={}
        response=data.get('response',{})
        if response.get('min_db') is not None and response.get('max_db') is not None:
            values['FR dB']=max(abs(response['min_db']),abs(response['max_db']))
        noise=data.get('noise',{})
        value=noise.get('normalized_noise_a_dbfs_rms',noise.get('noise_a_dbfs_rms'))
        if value is not None:values['Noise dB']=value
        value=data.get('dynamic_range',{}).get('dynamic_range_a_db')
        if value is not None:values['Dynamic range dB']=-value
        value=data.get('tone',{}).get('thd_pct')
        if value is not None:values['THD %']=value
        value=data.get('imd_smpte',{}).get('imd_noise_pct')
        if value is not None:values['IMD + noise %']=value
        scores[side]={}
        for name,value in values.items():
            try:value=float(value)
            except (TypeError,ValueError):continue
            if not math.isfinite(value) or (name.endswith('%') and value<0):continue
            scores[side][name]=next((i for i,bound in enumerate(LIMITS[name]) if value<=bound),4)
    for name in LIMITS:
        if any(name in scores.get(side,{}) for side in ('left','right')):
            rows.append(('Open Lab rating — '+name,*[LABELS[scores[side][name]] if name in scores.get(side,{}) else '—' for side in ('left','right')]))
    if rows:
        rows.append(('Open Lab rating — measured tests only',*[LABELS[max(scores[side].values())] if scores.get(side) else '—' for side in ('left','right')]))
    return rows
