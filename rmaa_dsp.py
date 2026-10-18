"""Independent, deterministic audio measurement algorithms for RMAA Open Lab.

Math, signal generation, and analysis only; no audio hardware required.
Results are not claimed to match RightMark's unpublished algorithms.
"""
from __future__ import annotations
import math
import numpy as np
from scipy import signal

EPS = 1e-18

def _array(x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1 or x.size == 0 or not np.isfinite(x).all():
        raise ValueError("Expected finite non-empty mono audio")
    return x

def rms(x):
    return float(np.sqrt(np.mean(_array(x)**2)))

def db(x):
    return 20 * math.log10(max(float(x), EPS))

def bandlimit(x, fs, lo=20.0, hi=20000.0):
    """Rectangular FFT measurement band, unity gain at every retained bin.

    Frequencies at both named endpoints are included. Unlike a forward/backward
    analogue-style filter this does not attenuate the measured band edges by
    6 dB. This is a finite-record spectral integration convention, not a claim
    about RightMark's unpublished filter. DC and out-of-band bins are removed.
    """
    if fs < 8000:
        raise ValueError("Minimum sample rate 8000 Hz")
    top = min(hi, 0.46*fs)
    if lo <= 0 or top <= lo:
        raise ValueError("Invalid analysis bandwidth")
    x = _array(x)
    if x.size < 1000:
        raise ValueError("Too short for filtered measurement")
    frequencies = np.fft.rfftfreq(len(x), 1/fs)
    spectrum = np.fft.rfft(x)
    tolerance = 16*np.finfo(float).eps*fs
    spectrum[(frequencies < lo-tolerance) | (frequencies > top+tolerance)] = 0
    return np.fft.irfft(spectrum, n=len(x))


def a_weight_sos(fs):
    """IEC A-weighting analogue poles/zeros bilinear transformed at sample rate."""
    if fs < 32000:
        raise ValueError("A-weighting requires fs >= 32 kHz")
    f1, f2, f3, f4 = 20.598997, 107.65265, 737.86223, 12194.217
    poles = -2*np.pi*np.array([f1,f1,f2,f3,f4,f4],dtype=float)
    zeros = np.zeros(4, dtype=float)
    # Normalize the digital filter to exactly 0 dB gain at 1000 Hz.
    z,p,k = signal.bilinear_zpk(zeros, poles, (2*np.pi*f4)**2, fs=fs)
    sos = signal.zpk2sos(z,p,k)
    _, h = signal.sosfreqz(sos, worN=[1000.0], fs=fs)
    sos[0, :3] /= abs(h[0])
    return sos

def a_weight_gain(frequencies):
    f=np.asarray(frequencies,dtype=float)
    def response(hz):
        q=hz*hz
        return (12194.217**2*q*q)/((q+20.598997**2)*np.sqrt((q+107.65265**2)*(q+737.86223**2))*(q+12194.217**2))
    return response(f)/response(1000.)


def a_weight(x, fs):
    # Analogue IEC magnitude evaluated at FFT bins: avoids bilinear frequency
    # warping near Nyquist, and avoids the doubled dB response of filtfilt.
    x=_array(x)
    frequencies=np.fft.rfftfreq(len(x),1/fs)
    return np.fft.irfft(np.fft.rfft(x)*a_weight_gain(frequencies),n=len(x))


def noise_stats(x, fs, weighted=True):
    y = bandlimit(x,fs)
    a = rms(y)
    out = {
      "noise_dbfs_rms": db(a), "noise_rms": a,
      "peak_dbfs": db(np.max(np.abs(_array(x)))),
      "dc_offset": float(np.mean(x)),
      "band_hz": [20.0, min(20000.0, 0.46*fs)]
    }
    if weighted:
        y2 = a_weight(y, fs)
        out["noise_a_dbfs_rms"] = db(rms(y2))
    return out

_FIT_BLOCK = 16384


def _fit_basis(start, stop, n, fs, freq):
    # Centered time reduces phase argument error on long, high-rate captures.
    t=(np.arange(start,stop,dtype=np.float64)-(n-1)/2)/fs
    a=np.ones((stop-start,1+2*len(freq)),dtype=np.float64)
    for i,f in enumerate(freq):
        phase=2*np.pi*f*t
        a[:,1+2*i]=np.sin(phase)
        a[:,2+2*i]=np.cos(phase)
    return a


def _linear_tone_fit(x, fs, freq):
    """Bounded-block least squares, with QR fallback for near-collinear tones."""
    n=len(x); width=1+2*len(freq)
    gram=np.zeros((width,width)); rhs=np.zeros(width)
    for start in range(0,n,_FIT_BLOCK):
        stop=min(n,start+_FIT_BLOCK)
        a=_fit_basis(start,stop,n,fs,freq)
        gram+=a.T@a
        rhs+=a.T@x[start:stop]
    # Normal equations are stable for separated tones. Near-coincident tones
    # need QR: do not square their condition number or retain an N x K matrix.
    if np.linalg.cond(gram)<1e7:
        coeff=np.linalg.solve(gram,rhs)
    else:
        from scipy.linalg import qr
        r=np.empty((0,width)); transformed=np.empty(0)
        for start in range(0,n,_FIT_BLOCK):
            stop=min(n,start+_FIT_BLOCK)
            a=_fit_basis(start,stop,n,fs,freq)
            matrix=np.vstack((r,a))
            values=np.concatenate((transformed,x[start:stop]))
            q,r=qr(matrix,mode="economic",check_finite=False)
            transformed=q.T@values
        coeff=np.linalg.lstsq(r,transformed,rcond=1e-12)[0]
    residual=np.empty(n,dtype=np.float64)
    for start in range(0,n,_FIT_BLOCK):
        stop=min(n,start+_FIT_BLOCK)
        residual[start:stop]=x[start:stop]-_fit_basis(start,stop,n,fs,freq)@coeff
    return coeff,residual,gram


def sine_fit(x, fs, frequencies):
    """Simultaneous sinusoidal fit; working matrix size is independent of N."""
    x=_array(x)
    freq=np.asarray(frequencies,dtype=float)
    if (freq.ndim!=1 or len(freq)==0 or not np.isfinite(freq).all()
            or np.any(freq<=0) or np.any(freq>=.5*fs)):
        raise ValueError("Invalid frequencies")
    coeff,residual,_=_linear_tone_fit(x,fs,freq)
    amps=np.hypot(coeff[1::2],coeff[2::2])
    return amps,residual,float(coeff[0])


def fit_frequency(x,fs,expected,companion_frequencies=()):
    """Coarse localization followed by analytic variable-projection refinement.

    A frequency error of microhertz can limit long-record THD+N to -90 dB.
    Refinement operates on frequency increments rather than the optimizer's
    absolute-frequency termination tolerance. Only one sinusoid is removed.
    """
    from scipy.optimize import minimize_scalar
    x=_array(x)
    if not 0<expected<fs/2:raise ValueError("Invalid expected frequency")
    nominal=np.asarray([*companion_frequencies,expected],dtype=float)
    factors=nominal/expected
    bound=max(.2,expected*.0015)
    frequencies=np.fft.rfftfreq(len(x),1/fs)
    spectrum=abs(np.fft.rfft((x-np.mean(x))*signal.windows.hann(len(x),sym=False)))
    candidates=np.flatnonzero(abs(frequencies-expected)<=bound+fs/len(x))
    coarse=float(frequencies[candidates[np.argmax(spectrum[candidates])]])
    half=.7*fs/len(x)
    def objective(f):
        _,residual,_=_linear_tone_fit(x,fs,factors*f)
        return float(np.dot(residual,residual))
    optimum=minimize_scalar(objective,bounds=(max(1e-9,coarse-half),min(fs/2-1e-9,coarse+half)),
                            method="bounded",options={"xatol":1e-8})
    f=float(optimum.x)
    for _ in range(8):
        coeff,residual,gram=_linear_tone_fit(x,fs,factors*f)
        projection=np.zeros(1+2*len(factors)); slope_norm=0.; numerator=0.
        for start in range(0,len(x),_FIT_BLOCK):
            stop=min(len(x),start+_FIT_BLOCK)
            a=_fit_basis(start,stop,len(x),fs,factors*f)
            t=(np.arange(start,stop)-(len(x)-1)/2)/fs
            slope=np.zeros(stop-start)
            for i,factor in enumerate(factors):
                slope+=factor*(coeff[1+2*i]*a[:,2+2*i]-coeff[2+2*i]*a[:,1+2*i])
            slope*=2*np.pi*t
            projection+=a.T@slope
            slope_norm+=float(np.dot(slope,slope))
            numerator+=float(np.dot(slope,residual[start:stop]))
        denominator=slope_norm-float(projection@np.linalg.solve(gram,projection))
        if denominator<=np.finfo(float).tiny:break
        delta=numerator/denominator
        updated=f+delta
        if not 0<updated<fs/2 or abs(delta)>half:break
        if updated==f:break
        f=updated
    return f


def tone_metrics(x, fs, f=997.0, max_harmonic=64):
    """THD as harmonic RMS/fundamental RMS; THD+N in 20 Hz to 20 kHz band."""
    if len(x)<fs//4: raise ValueError("Tone recording too short")
    # Remove the carrier BEFORE filtering. Filtering a large sinusoid first
    # creates boundary transients which masquerade as noise at -70..-90 dB.
    y=_array(x)
    cut=min(int(.12*fs),len(y)//5)
    if cut:y=y[cut:-cut]
    nominal_f=f
    f=fit_frequency(y,fs,f)
    harmonic_top=min(20000.,0.46*fs)
    harmonics=[f]+[f*n for n in range(2,max_harmonic+1) if f*n <= harmonic_top+1e-7]
    amp, residual, dc=sine_fit(y,fs,harmonics)
    fundamental=max(float(amp[0]), EPS)
    thd_ratio=float(np.linalg.norm(amp[1:]))/fundamental
    _, noise_and_distortion, _=sine_fit(y,fs,[f])
    noise_and_distortion=bandlimit(noise_and_distortion,fs)
    edge=min(int(.15*fs),len(noise_and_distortion)//5)
    if edge:noise_and_distortion=noise_and_distortion[edge:-edge]
    thdn_ratio=math.sqrt(2)*rms(noise_and_distortion)/fundamental
    thdn_a_ratio=None
    if fs>=32000:
        weighted=a_weight(noise_and_distortion,fs)
        edge=min(int(.04*fs),len(weighted)//6)
        if edge: weighted=weighted[edge:-edge]
        fundamental_a=fundamental*max(float(a_weight_gain(f)),EPS)
        thdn_a_ratio=math.sqrt(2)*rms(weighted)/fundamental_a
    return {
        "frequency_hz":nominal_f, "measured_frequency_hz":f, "fundamental_dbfs_peak": db(fundamental),
        "fundamental_dbfs_rms":db(fundamental / math.sqrt(2)),
        "thd_pct":100*thd_ratio if len(harmonics)>1 else None, "thd_db":db(thd_ratio) if len(harmonics)>1 else None,
        "thdn_pct":100*thdn_ratio, "thdn_db":db(thdn_ratio),
        "thdn_a_db":db(thdn_a_ratio) if thdn_a_ratio is not None else None,
        "harmonic_levels_db": [db(v/fundamental) for v in amp[1:]],
        "harmonic_frequencies_hz":harmonics[1:],
        "thd_band_hz":[20.,min(20000.,.46*fs)],"max_harmonic":max_harmonic,
        "unweighted_residual_dbfs_rms": db(rms(noise_and_distortion)),
        "residual_dc_after_bandpass": dc,
    }

def dynamic_range(x, fs, f=997.0, tone_nominal_dbfs=-60):
    """Pilot at -60 dBFS peak; extrapolate to 0 dBFS sinusoidal RMS."""
    y=_array(x)
    cut=min(int(.12*fs),len(y)//5)
    if cut:y=y[cut:-cut]
    f=fit_frequency(y,fs,f)
    amps,residual,_=sine_fit(y,fs,[f])
    residual=bandlimit(residual,fs)
    edge=min(int(.15*fs),len(residual)//5)
    if edge:residual=residual[edge:-edge]
    pilot_rms=amps[0]/math.sqrt(2)
    noise_rms=rms(residual)
    if pilot_rms<EPS: raise ValueError("Pilot tone absent")
    noise_a_rms=None
    if fs>=32000:
        weighted=a_weight(residual,fs)
        edge=min(int(.04*fs),len(weighted)//6)
        if edge: weighted=weighted[edge:-edge]
        noise_a_rms=rms(weighted)
    return {
        "pilot_dbfs_rms":db(pilot_rms),
        "residual_noise_dbfs_rms":db(noise_rms),
        "dynamic_range_sine_rms_db":db(pilot_rms/max(noise_rms,EPS)) - tone_nominal_dbfs,
        "dynamic_range_db":db(math.sqrt(2)*pilot_rms/max(noise_rms,EPS)) - tone_nominal_dbfs,
        "dynamic_range_a_db":(db(math.sqrt(2)*pilot_rms/max(noise_a_rms,EPS)) - tone_nominal_dbfs) if noise_a_rms is not None else None,
        "nominal_pilot_dbfs_peak":tone_nominal_dbfs,
        "method":"full-scale PEAK reference extrapolated from nominal -60 dBFS pilot; sine-RMS value stored separately"
    }

def _normalize_peak(x, level_dbfs):
    if not -120<=level_dbfs<=0: raise ValueError("Invalid or excessive test level")
    x=np.asarray(x,dtype=np.float64)
    return (x/max(np.max(np.abs(x)), EPS)*10**(level_dbfs/20)).astype(np.float32)

def tone(fs=48000, f=997.0, seconds=1.0, peak_dbfs=-18.0):
    t=np.arange(round(fs*seconds))/fs
    return _normalize_peak(np.sin(2*np.pi*f*t),peak_dbfs)

def multitone(fs=48000, seconds=2.0, n_tones=48, peak_dbfs=-18.0):
    """Coherent, phase-optimized multitone with exact FFT bins and safe peak limit."""
    n=round(fs*seconds)
    if n<fs or n>fs*15: raise ValueError("Duration 1...15 sec")
    if n_tones < 8 or n_tones > 180: raise ValueError("8...180 tones")
    bins=np.unique(np.rint(np.geomspace(20,min(20000,0.44*fs),n_tones)*n/fs).astype(int))
    bins=bins[(bins>1)&(bins<n//2)]
    t=np.arange(n)
    out=np.zeros(n,dtype=np.float64)
    for j,k in enumerate(bins):
        out+=np.sin(2*np.pi*k*t/n + np.pi*j*(j-1)/len(bins))
    return _normalize_peak(out,peak_dbfs), bins*fs/n

def multitone_response(reference, measured, fs, frequencies, lo=40, hi=15000):
    """Amplitude ratio by coherent projection, referenced to 1 kHz (non-RMAA-specific)."""
    x=_array(reference)
    y=_array(measured)
    if len(x)!=len(y):
        raise ValueError("Pass aligned mono samples of equal length")
    # A harmonic frequency component is compared to its exact known generated tone.
    n=len(x)
    bins=np.rint(np.asarray(frequencies)*n/fs).astype(int)
    if np.any(bins<1) or np.any(bins>n//2):
        raise ValueError("Invalid multitone bins")
    fx=np.fft.rfft(x)
    fy=np.fft.rfft(y)
    ratio=np.abs(fy[bins])/np.maximum(np.abs(fx[bins]), EPS)
    frequency=bins*fs/n
    db_gain=20*np.log10(np.maximum(ratio,EPS))
    anchor=np.interp(1000,frequency,db_gain)
    relative=db_gain-anchor
    band=(frequency>=lo)&(frequency<=hi)
    if not np.any(band): raise ValueError("No tones in analysis band")
    return {
      "frequency_hz":frequency.tolist(), "response_db":relative.tolist(),
      "min_db":float(np.min(relative[band])),
      "max_db":float(np.max(relative[band])),
      "reference_1000hz_db":float(anchor),
      "band_hz":[lo,hi],
      "min_20_20000_db":float(np.min(relative[(frequency>=20)&(frequency<=20000)])),
      "max_20_20000_db":float(np.max(relative[(frequency>=20)&(frequency<=20000)])),
    }

def imd_signal(fs, mode="smpte", seconds=1.0, peak_dbfs=-18, high_hz=7000.0):
    if mode == "smpte":
        f1,f2=60.,float(high_hz)
        ratio=4.
    elif mode == "ccif":
        f1,f2=19000.,20000.
        ratio=1.
    elif mode in ("ccif_10k","ccif_sweep"):
        center=10000. if mode=="ccif_10k" else float(high_hz)
        f1,f2=center-500.,center+500.
        ratio=1.
    else: raise ValueError("Unknown IMD generator mode")
    if f2>=fs*.46: raise ValueError("Need higher sample rate")
    t=np.arange(round(fs*seconds))/fs
    s=ratio*np.sin(2*np.pi*f1*t)+np.sin(2*np.pi*f2*t)
    return _normalize_peak(s,peak_dbfs)

def imd_metrics(x,fs,mode="smpte",high_hz=7000.0):
    """IMD percentage defined explicitly by chosen set of intermod components."""
    if mode=="smpte":
        high_hz=float(high_hz)
        carrier=(60.,high_hz)
        products=[high_hz-120,high_hz-60,high_hz+60,high_hz+120]
        denominator="high-tone amplitude"
    elif mode=="ccif":
        carrier=(19000.,20000.)
        products=[1000.,18000.,21000.] # f2-f1, 2f1-f2, 2f2-f1
        denominator="RMS of the two equal fundamental amplitudes"
    elif mode in ("ccif_10k","ccif_sweep"):
        center=10000. if mode=="ccif_10k" else float(high_hz)
        carrier=(center-500.,center+500.)
        products=[1000.,center-1500.,center+1500.]
        denominator="RMS of the two equal fundamental amplitudes"
    else:raise ValueError("Unknown IMD mode")
    top=min(20000.,.46*fs)
    if min(carrier)<=0 or max(carrier)>=fs*.49:
        raise ValueError("Sample rate insufficient for this IMD measurement")
    # Negative difference frequencies are real positive-frequency components.
    # Reject DC, components outside the stated band, and carrier aliases. Near
    # sweep boundaries several symbolic products name the same physical tone.
    unique=[]
    for frequency in products:
        frequency=abs(float(frequency))
        if not 20.<=frequency<=top:continue
        if any(abs(frequency-f)<1e-7 for f in (*carrier,*unique)):continue
        unique.append(frequency)
    products=unique
    yf=_array(x)
    edge=min(int(.18*fs),len(yf)//5)
    if edge:yf=yf[edge:-edge]
    nominal_carrier=carrier
    ratio=fit_frequency(yf,fs,carrier[-1],companion_frequencies=carrier[:-1])/carrier[-1]
    carrier=tuple(f*ratio for f in carrier)
    products=[f*ratio for f in products]
    amps,_,_=sine_fit(yf,fs,[*carrier,*products])
    a,b=amps[:2]
    signal_reference=(b if mode=="smpte" else np.hypot(a,b))
    value=float(np.linalg.norm(amps[2:]))/max(signal_reference,EPS)
    _,combined_residual,_=sine_fit(yf,fs,list(carrier))
    combined_residual=bandlimit(combined_residual,fs,lo=20,hi=min(20000,.46*fs))
    trim=min(int(.15*fs),len(combined_residual)//5)
    if trim:combined_residual=combined_residual[trim:-trim]
    imd_noise_ratio=math.sqrt(2)*rms(combined_residual)/max(signal_reference,EPS)
    imd_noise_a_ratio=math.sqrt(2)*rms(a_weight(combined_residual,fs))/max(signal_reference,EPS)
    return {
      "mode":mode,"carrier_hz":list(nominal_carrier),"measured_carrier_hz":list(carrier),
      "products_hz":products,
      "imd_pct":float(value*100),
      "imd_db":db(value),
      "imd_noise_pct":float(imd_noise_ratio*100),
      "imd_noise_db":db(imd_noise_ratio),
      "imd_noise_a_pct":float(imd_noise_a_ratio*100),
      "imd_noise_a_db":db(imd_noise_a_ratio),
      "fundamentals_dbfs_peak":[db(a),db(b)],
      "product_levels_dbfs_peak":[db(v) for v in amps[2:]],
      "denominator":denominator,
      "analysis_band_hz":[20.,top],
      "product_selection":"unique absolute difference products inside analysis band, excluding DC and carriers",
      "method":"selected spectral components by simultaneous sinusoidal least squares"
    }

def crosstalk(primary, secondary, fs, f=997.0):
    """Measured channel isolation; negative dB numbers mean leakage below main signal."""
    a,_,_=sine_fit(bandlimit(primary,fs),fs,[f])
    b,_,_=sine_fit(bandlimit(secondary,fs),fs,[f])
    return {"crosstalk_db":db(b[0]/max(a[0],EPS)),
            "primary_dbfs_peak":db(a[0]),
            "leak_dbfs_peak":db(b[0]), "frequency_hz":f}

def stereo_balance(left,right,fs,f=997.0):
    a,_,_=sine_fit(bandlimit(left,fs),fs,[f])
    b,_,_=sine_fit(bandlimit(right,fs),fs,[f])
    return {"right_minus_left_db":db(max(b[0],EPS)/max(a[0],EPS)),
            "left_dbfs_peak":db(a[0]),"right_dbfs_peak":db(b[0])}

def delay_impulse(reference,measured,fs, max_delay_seconds=.3):
    """Cross-correlation delay and per-frequency transfer phase from aligned data."""
    a=_array(reference)
    b=_array(measured)
    if len(a)<fs//10 or len(b)<fs//10:raise ValueError("Insufficient samples")
    # Correlation to estimate non-periodic latency; limit to positive or negative 0.3 s.
    corr=signal.correlate(b,a,method="fft",mode="full")
    lags=signal.correlation_lags(len(b),len(a),mode="full")
    valid=(abs(lags)<=int(max_delay_seconds*fs))
    delay=int(lags[valid][np.argmax(abs(corr[valid]))])
    if delay>=0:
        aa=a[:min(len(a),len(b)-delay)]
        bb=b[delay:delay+len(aa)]
    else:
        aa=a[-delay:]
        bb=b[:len(aa)]
    n=min(len(aa),len(bb))
    if n<fs//10:raise ValueError("No overlapping recordings after delay")
    aa=aa[:n];bb=bb[:n]
    f, cross=signal.csd(aa,bb,fs=fs,nperseg=min(8192,n))
    _, power=signal.welch(aa,fs=fs,nperseg=min(8192,n))
    _, coherence=signal.coherence(aa,bb,fs=fs,nperseg=min(8192,n))
    h=cross/np.maximum(power,EPS)
    phase=np.angle(h,deg=True)
    impulse=np.fft.irfft(h,n=2*(len(h)-1))
    impulse=np.fft.fftshift(impulse)
    time_ms=(np.arange(len(impulse))-len(impulse)//2)*1000/fs
    return {
        "latency_samples":delay, "latency_ms":delay*1000/fs,
        "frequency_hz":f.tolist(),
        "transfer_phase_deg":phase.tolist(),
        "coherence":coherence.tolist(),
        "impulse_time_ms":time_ms.tolist(),
        "impulse_amplitude":impulse.tolist(),
        "gain_db":(20*np.log10(np.maximum(abs(h),EPS))).tolist(),
        "method":"FFT cross-correlation + cross-spectral phase after latency compensation",
    }
