"""Dense periodic multisine best linear approximation (BLA).

This is an independent finite-record method, not RightMark's private method.
Period repeatability and phase-realization spread are not coherence. Residual
sample-clock error is checked before coherent averaging; it is never corrected
silently here. Caller must align/resample the capture explicitly beforehand.
"""
from __future__ import annotations
import math
import numpy as np
from scipy.optimize import minimize_scalar


class DenseResponseClockError(ValueError):
    def __init__(self, ppm, limit, signed_ppm=None):
        self.residual_ppm=float(ppm)
        self.limit_ppm=float(limit)
        self.signed_ppm=float(ppm if signed_ppm is None else signed_ppm)
        super().__init__(f"Residual clock drift {ppm:.6f} ppm exceeds dense-response limit {limit:.6f} ppm; refine clock alignment before analysis")


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        from rmaa_transport import MeasurementCancelled
        raise MeasurementCancelled("Measurement cancelled")


def _settings(fs, period_seconds, repeats, seeds, peak_dbfs):
    if not isinstance(fs,(int,np.integer)) or not 8000<=fs<=384000:
        raise ValueError("Sample rate must be an integer in 8000..384000")
    if not .25<=period_seconds<=2. or abs(round(fs*period_seconds)-fs*period_seconds)>1e-7:
        raise ValueError("Period must contain an integer frame count and last .25..2 s")
    if not isinstance(repeats,int) or not 4<=repeats<=16:
        raise ValueError("Use 4..16 periods per phase realization")
    if not 1<=len(seeds)<=4 or len(set(seeds))!=len(seeds):
        raise ValueError("Use 1..4 distinct phase seeds")
    if not np.isfinite(peak_dbfs) or not -36<=peak_dbfs<=0:
        raise ValueError("Peak level must be -36..0 dBFS")
    n=round(fs*period_seconds)
    low=math.ceil(20*n/fs);high=math.floor(min(20000.,.46*fs)*n/fs)
    if high<=low:raise ValueError("Insufficient excitation bandwidth")
    return n,low,high


def _period(fs,n,low,high,seed,peak_dbfs,version=2,cancel=None):
    rng=np.random.default_rng(int(seed))
    spectrum=np.zeros(n//2+1,dtype=np.complex128)
    spectrum[low:high+1]=np.exp(2j*np.pi*rng.random(high-low+1))
    origin=0.
    if version==2:
        # Move the continuous waveform's largest absolute peak onto sample 0.
        # This changes the time origin, not the relative tone phases or level
        # control. A coarse sampled maximum alone can miss a competing peak.
        oversampling=8
        dense=np.fft.irfft(spectrum,n=n*oversampling)*oversampling
        absolute=abs(dense)
        candidates=np.flatnonzero((absolute>=np.roll(absolute,1)) &
                                  (absolute>=np.roll(absolute,-1)) &
                                  (absolute>=.97*float(np.max(absolute))))
        frequencies=np.arange(low,high+1,dtype=np.float64)
        omega=2*np.pi*frequencies/n
        coefficients=spectrum[low:high+1].copy()
        def value(position):
            return float((2/n)*np.sum(coefficients*np.exp(1j*omega*position)).real)
        best=-1.
        for index in candidates:
            _check_cancel(cancel)
            center=float(index)/oversampling
            # Optimize a small increment; absolute-position tolerances would
            # lose sub-sample precision when the peak lies late in the period.
            optimum=minimize_scalar(lambda delta:-abs(value(center+delta)),
                bounds=(-1/oversampling,1/oversampling),method='bounded',
                options={'xatol':1e-12})
            position=center+float(optimum.x)
            magnitude=abs(value(position))
            if magnitude>best:best=magnitude;origin=position % n
        spectrum[low:high+1]*=np.exp(1j*omega*origin)
        period=np.fft.irfft(spectrum,n=n)
        divisor=best
    elif version==1:
        period=np.fft.irfft(spectrum,n=n)
        divisor=float(np.max(abs(period)))
    else:
        raise ValueError('Unsupported dense-response generator version')
    period*=10**(peak_dbfs/20)/divisor
    return period.astype(np.float32),origin


def generate_dense_response(fs=48000,period_seconds=1.,repeats=8,
                            seeds=(20261018,20261019),peak_dbfs=0.,cancel=None,generator_version=2):
    """Return mono stimulus + JSON-safe metadata; default length is 16 s.

    Every retained positive-frequency FFT bin is excited with equal magnitude.
    Each seed is normalized to the requested sample peak, including 0 dBFS.
    No attenuation, fade, clipping, or hidden output-level adjustment is added.
    """
    seeds=tuple(int(seed) for seed in seeds)
    n,low,high=_settings(fs,period_seconds,repeats,seeds,peak_dbfs)
    audio=np.empty(n*repeats*len(seeds),dtype=np.float32)
    origins=[]
    rms_levels=[]
    crest_factors=[]
    for index,seed in enumerate(seeds):
        _check_cancel(cancel)
        period,origin=_period(fs,n,low,high,seed,peak_dbfs,version=generator_version,cancel=cancel)
        origins.append(float(origin))
        period_rms=float(np.sqrt(np.mean(period.astype(float)**2)))
        rms_levels.append(20*math.log10(period_rms))
        crest_factors.append(20*math.log10(float(np.max(abs(period)))/period_rms))
        for repeat in range(repeats):
            start=(index*repeats+repeat)*n
            audio[start:start+n]=period
    return audio,{
        "method":f"dense_periodic_multisine_bla_v{generator_version}","fs":int(fs),
        "generator_version":generator_version,"time_origin_samples":origins,
        "true_peak_method":"8x spectral search with continuous candidate refinement; absolute maximum placed on sample grid" if generator_version==2 else "legacy sample-peak normalization",
        "period_frames":n,"period_seconds":n/fs,"repeats":repeats,
        "settling_periods":1,"phase_seeds":list(seeds),
        "first_bin":low,"last_bin":high,"frequency_spacing_hz":fs/n,
        "band_hz":[low*fs/n,high*fs/n],"peak_dbfs":float(peak_dbfs),
        "frames":len(audio),"analysis_periods_per_seed":repeats-1,
        "realization_rms_dbfs":rms_levels,"realization_crest_db":crest_factors,
        "interpretation":"phase-realization averaged linear approximation at the stated sample peak; peak-normalized realizations have their reported RMS levels, so nonlinear DUTs can differ between seeds and from single-tone response",
        "quality_interpretation":"repeatability and realization spread, not coherence or proof of absolute calibration",
    }


def _relative_delay(current,previous,frequencies,fs,n):
    """Subsample time shift from broad-band phase; no interpolation of audio."""
    cross=current*np.conj(previous)
    # Exclude nonfinite or negligible bins instead of unwrapping noisy phases
    # across deep notches. Natural cross-power weighting downweights those bins.
    if not np.isfinite(cross).all() or np.sum(abs(cross))<1e-30:
        raise ValueError("Insufficient periodic input signal")
    full=np.zeros(n//2+1,dtype=np.complex128)
    bins=np.rint(frequencies*n/fs).astype(int)
    full[bins]=cross
    correlation=np.fft.irfft(full,n=n)
    radius=min(n//8,max(2,math.ceil(.002*n)))
    lags=np.arange(-radius,radius+1)
    coarse=int(lags[np.argmax(correlation[lags % n])])
    angular=2*np.pi*frequencies/fs
    scale=float(np.sum(abs(cross)))
    def objective(lag):
        return -float(np.sum(cross*np.exp(1j*angular*lag)).real)/scale
    result=minimize_scalar(objective,bounds=(coarse-1.,coarse+1.),method="bounded",options={"xatol":1e-10})
    return float(result.x)


def analyze_dense_response(reference,measured,fs,metadata,*,max_clock_ppm=.005,cancel=None):
    """Estimate a BLA with one-period working buffers, retaining no raw audio.

    Reference and capture must already share a time base. A constant delay is
    harmless after settling, but residual clock drift mixes the occupied bins.
    Excess drift raises DenseResponseClockError rather than publishing a precise
    looking curve from an invalid coherent measurement. None disables this
    guard only for explicit diagnostics and is reported in the result.
    """
    if metadata.get("method") not in ("dense_periodic_multisine_bla_v1","dense_periodic_multisine_bla_v2"):
        raise ValueError("Unsupported dense-response metadata")
    n=int(metadata['period_frames']);repeats=int(metadata['repeats'])
    seeds=tuple(metadata['phase_seeds']);settle=int(metadata['settling_periods'])
    checked=_settings(fs,n/fs,repeats,seeds,float(metadata['peak_dbfs']))
    if checked!=(n,int(metadata['first_bin']),int(metadata['last_bin'])) or metadata['fs']!=fs:
        raise ValueError("Inconsistent dense-response metadata")
    if settle!=1 or max_clock_ppm is not None and (not np.isfinite(max_clock_ppm) or max_clock_ppm<=0):
        raise ValueError("Invalid analysis settings")
    x=np.asarray(reference);y=np.asarray(measured)
    total=n*repeats*len(seeds)
    if x.ndim!=1 or y.ndim!=1 or len(x)!=total or len(y)!=total:
        raise ValueError("Dense-response stage length/channel mismatch")
    low,high=checked[1:];bins=np.arange(low,high+1)
    frequencies=bins*fs/n
    estimates=[];noise_variances=[];clock=[];period_scatter=[];reference_powers=[]
    for group in range(len(seeds)):
        _check_cancel(cancel)
        start=group*repeats*n
        ref=np.asarray(x[start:start+n],dtype=np.float64)
        if not np.isfinite(ref).all():raise ValueError("Nonfinite reference")
        spectrum=np.fft.rfft(ref)[bins]
        if np.min(abs(spectrum))<1e-12:raise ValueError("Reference has unexcited measurement bins")
        count=0;mean=np.zeros(len(bins),dtype=np.complex128);m2=np.zeros(len(bins))
        previous=None;amplitudes=[]
        for repeat in range(settle,repeats):
            _check_cancel(cancel)
            offset=start+repeat*n
            period=np.asarray(y[offset:offset+n],dtype=np.float64)
            if not np.isfinite(period).all():raise ValueError("Nonfinite capture")
            measured_fft=np.fft.rfft(period)[bins]
            if previous is not None:
                lag=_relative_delay(measured_fft,previous,frequencies,fs,n)
                clock.append(-lag/n*1e6)
            previous=measured_fft
            h=measured_fft/spectrum
            count+=1;delta=h-mean;mean+=delta/count
            m2+=(delta*np.conj(h-mean)).real
            amplitudes.append(20*np.log10(np.maximum(abs(h),1e-30)))
        estimates.append(mean)
        reference_powers.append(abs(spectrum)**2)
        noise_variances.append(np.maximum(m2,0.)/max(1,count-1)/count)
        period_scatter.append(np.std(np.asarray(amplitudes),axis=0,ddof=1))
    clock_ppm=float(np.median(clock)) if clock else 0.
    # Use both signed systematic drift and the largest per-step drift: opposite
    # excursions must not cancel, e.g. sample slips or a clock-rate change.
    clock_max=float(np.max(abs(np.asarray(clock)))) if clock else 0.
    if max_clock_ppm is not None and clock_max>max_clock_ppm:
        raise DenseResponseClockError(clock_max,max_clock_ppm,clock_ppm)
    estimates=np.asarray(estimates)
    powers=np.asarray(reference_powers)
    weights=powers/np.sum(powers,axis=0)
    # H1/cross-power BLA: peak-normalized seeds can have different RMS, so
    # weight their transfer estimates by the actual excitation power.
    h=np.sum(estimates*weights,axis=0)
    gain=20*np.log10(np.maximum(abs(h),1e-30))
    anchor=float(np.interp(1000.,frequencies,gain))
    response=gain-anchor
    # Complex noise has two quadratures; estimate standard error of radial
    # magnitude. This is a repeat-based estimate, not a guaranteed confidence CI.
    noise_var=np.sum(np.asarray(noise_variances)*weights**2,axis=0)
    noise_error=20/np.log(10)*np.sqrt(noise_var/2)/np.maximum(abs(h),1e-30)
    realization_db=20*np.log10(np.maximum(abs(estimates),1e-30))
    spread=np.ptp(realization_db,axis=0) if len(seeds)>1 else np.zeros(len(bins))
    band=(frequencies>=40)&(frequencies<=15000)
    return {
        "frequency_hz":frequencies.tolist(),"response_db":response.tolist(),
        "min_db":float(np.min(response[band])),"max_db":float(np.max(response[band])),
        "min_20_20000_db":float(np.min(response)),"max_20_20000_db":float(np.max(response)),
        "reference_1000hz_db":anchor,"band_hz":[40.,15000.],
        "measurement_band_hz":[float(frequencies[0]),float(frequencies[-1])],
        "frequency_spacing_hz":fs/n,"method":"dense periodic multisine H1/BLA; complex average over repeats, excitation-power-weighted phase realizations",
        "period_frames":n,"averaged_periods":(repeats-settle)*len(seeds),
        "phase_realizations":len(seeds),"settling_periods_per_realization":settle,
        "repeatability_std_db":np.mean(period_scatter,axis=0).tolist(),
        "random_noise_standard_error_db":noise_error.tolist(),
        "reference_random_noise_standard_error_db":float(np.interp(1000.,frequencies,noise_error)),
        "realization_spread_db":spread.tolist(),
        "clock_residual_ppm":clock_ppm,"clock_max_step_ppm":clock_max,
        "clock_validation_limit_ppm":max_clock_ppm,
        "clock_validation_passed":max_clock_ppm is not None,
        "quality_interpretation":"period repeatability and phase-realization spread are not coherence; random-noise standard error describes local unnormalized gain and excludes shared 1 kHz reference error, systematic distortion, calibration and resampler bias",
    }

# An arbitrary-ratio interpolator avoids giant rational up/down factors for
# sub-ppm correction. Coefficients are interpolated between dense phase tables;
# the coefficient table and block workspace do not grow with capture duration.
from functools import lru_cache


@lru_cache(maxsize=2)
def _sinc_table(taps=128,phases=4096):
    if taps not in (64,128,256):raise ValueError("Unsupported interpolation length")
    radius=taps//2
    offsets=np.arange(-radius+1,radius+1)
    fraction=np.arange(phases+1,dtype=np.float64)/phases
    distance=fraction[:,None]-offsets[None,:]
    window=np.i0(10.*np.sqrt(np.maximum(0.,1-(distance/radius)**2)))/np.i0(10.)
    window[abs(distance)>radius]=0.
    weights=np.sinc(distance)*window
    weights/=weights.sum(axis=1,keepdims=True)
    weights.flags.writeable=False
    offsets.flags.writeable=False
    return offsets,weights


def resample_clock_sinc(audio,input_per_output,output_frames=None,*,taps=128,cancel=None):
    """Finite arbitrary-clock interpolation: out[i] = in[i*input_per_output].

    Designed for clock corrections within +/-1%, with 128 taps by default.
    No rational approximation or hidden amplitude normalization is applied to
    audio. Edge samples use zero extension; keep settling periods when measuring.
    Coefficient DC normalization only removes fractional-phase kernel ripple.
    """
    x=np.asarray(audio)
    if x.ndim not in (1,2) or x.ndim==2 and not 1<=x.shape[1]<=8 or not len(x):
        raise ValueError("Expected nonempty mono or <=8 channel audio")
    if not np.issubdtype(x.dtype,np.floating):raise ValueError("Expected floating point audio")
    if not np.isfinite(input_per_output) or not .99<=input_per_output<=1.01:
        raise ValueError("Clock ratio must be within +/-1%")
    if output_frames is None:output_frames=int(math.floor((len(x)-1)/input_per_output))+1
    if not isinstance(output_frames,(int,np.integer)) or not 1<=output_frames<=int(len(x)*1.02)+256:
        raise ValueError("Invalid resampled length")
    if input_per_output==1. and output_frames==len(x):return x.copy()
    return _resample_position_blocks(x,output_frames,
        lambda start,stop:np.arange(start,stop,dtype=np.float64)*input_per_output,
        taps=taps,cancel=cancel)


def _resample_position_blocks(x,output_frames,position_function,*,taps=128,cancel=None):
    """Shared bounded interpolation for constant and explicitly tracked clocks."""
    offsets,table=_sinc_table(taps)
    phases=len(table)-1
    dtype=np.float32 if x.dtype==np.float32 else np.float64
    out=np.empty((output_frames,)+x.shape[1:],dtype=dtype)
    for start in range(0,output_frames,2048):
        _check_cancel(cancel)
        stop=min(output_frames,start+2048)
        position=np.asarray(position_function(start,stop),dtype=np.float64)
        if position.shape!=(stop-start,) or not np.isfinite(position).all():
            raise ValueError("Invalid clock interpolation positions")
        integer=np.floor(position).astype(np.int64)
        phase=(position-integer)*phases
        index=np.minimum(np.floor(phase).astype(int),phases-1)
        fraction=phase-index
        weights=table[index]*(1-fraction[:,None])+table[index+1]*fraction[:,None]
        source=integer[:,None]+offsets
        valid=(source>=0)&(source<len(x))
        source=np.clip(source,0,len(x)-1)
        weights*=valid
        samples=x[source]
        if x.ndim==1:
            out[start:stop]=np.einsum('bt,bt->b',weights,samples,optimize=True)
        else:
            out[start:stop]=np.einsum('bt,btc->bc',weights,samples,optimize=True)
    return out


def create_response(fs=48000,level_dbfs=0.,cancel=None):
    audio,meta=generate_dense_response(fs=fs,peak_dbfs=level_dbfs,cancel=cancel)
    meta['generator_method']=meta['method']
    meta['method']='periodic_dense_bla'
    return audio,meta


def _clock_nodes(reference, measured, fs, metadata, cancel=None):
    """Relative delay observations from H=Y/X, linking independent phase seeds."""
    n=metadata['period_frames'];repeats=metadata['repeats']
    bins=np.arange(metadata['first_bin'],metadata['last_bin']+1)
    frequencies=bins*fs/n
    nodes=[];delays=[];previous=None;cumulative=0.
    for group in range(len(metadata['phase_seeds'])):
        start=group*repeats*n
        ref=np.fft.rfft(np.asarray(reference[start:start+n],dtype=float))[bins]
        for repeat in range(1,repeats):
            _check_cancel(cancel)
            offset=start+repeat*n
            h=np.fft.rfft(np.asarray(measured[offset:offset+n],dtype=float))[bins]/ref
            if previous is not None:
                cumulative-=_relative_delay(h,previous,frequencies,fs,n)
            nodes.append(offset+(n-1)/2);delays.append(cumulative)
            previous=h
    return np.asarray(nodes),np.asarray(delays)


def _subperiod_clock_max(measured, fs, metadata, cancel=None):
    """Independent half-period repeat checks detect faster timing changes.

    Identical halves in successive repeats share the DUT response. This does
    not compare different excitation seeds and is not spectral coherence.
    """
    n=metadata['period_frames'];repeats=metadata['repeats'];half=n//2
    window=np.hanning(half)
    frequencies=np.fft.rfftfreq(half,1/fs)
    keep=(frequencies>=20)&(frequencies<=min(20000.,.46*fs))
    frequencies=frequencies[keep]
    maximum=0.
    for group in range(len(metadata['phase_seeds'])):
        start=group*repeats*n
        for fraction in (0,n-half):
            previous=None
            for repeat in range(1,repeats):
                _check_cancel(cancel)
                offset=start+repeat*n+fraction
                part=np.asarray(measured[offset:offset+half],dtype=float)*window
                got=np.fft.rfft(part)[keep]
                if previous is not None:
                    lag=_relative_delay(got,previous,frequencies,fs,half)
                    maximum=max(maximum,abs(lag)/n*1e6)
                previous=got
    return float(maximum)


def _track_period_clock(reference,wave,fs,metadata,context_before,context_after,cancel=None):
    """One explicit smooth timing warp, retaining real surrounding samples."""
    from scipy.interpolate import CubicSpline
    before=np.asarray(context_before) if context_before is not None else np.empty(0)
    after=np.asarray(context_after) if context_after is not None else np.empty(0)
    if (before.ndim!=1 or after.ndim!=1 or len(before)<128 or len(after)<128
            or not np.isfinite(before).all() or not np.isfinite(after).all()):
        raise ValueError('Period clock tracking requires >=128 real context samples before and after the response stage')
    nodes,delays=_clock_nodes(reference,wave,fs,metadata,cancel)
    if len(nodes)<4 or not np.isfinite(delays).all():raise ValueError('Insufficient clock tracking observations')
    # A sample slip is far larger than this supported sub-ppm smooth trajectory.
    if np.max(abs(np.diff(delays)/np.diff(nodes)))*1e6>.2:
        raise ValueError('Abrupt or excessive timing change; smooth period clock tracking rejected')
    function=CubicSpline(nodes,delays,bc_type='natural',extrapolate=True)
    # Limit context and workspace independently of recording duration. Keep the
    # interpolation kernel plus the whole inferred displacement available.
    padding=max(128,66+int(math.ceil(np.max(abs(delays)))))
    if len(before)<padding or len(after)<padding:raise ValueError('Insufficient clock tracking context for inferred delay')
    context=np.concatenate((before[-padding:],np.asarray(wave),after[:padding]))
    def positions(start,stop):
        nominal=np.arange(start,stop,dtype=float)
        # Invert the inferred tiny smooth displacement by one fixed-point step.
        shifted=nominal-function(nominal)
        return padding+nominal-function(shifted)
    corrected=_resample_position_blocks(context,len(wave),positions,cancel=cancel)
    return corrected,{
        'method':'period-center relative H=Y/X delays; natural cubic timing trajectory; one 128-tap arbitrary-position sinc interpolation',
        'assumption':'DUT response is stable across repeats and phase seeds; smooth common broadband delay changes are assigned to clock timing, not independently proven hardware clock changes',
        'scope':'frequency-response magnitude only; other metrics and stereo phase use the original globally aligned capture',
        'node_frames':nodes.tolist(),'relative_delay_samples':delays.tolist(),
        'delay_range_samples':float(np.ptp(delays)),
        'maximum_supported_step_ppm':.2,'context_samples_each_side':padding,
        'correction_passes':1,
    }


def _quality_annotations(result,tracked=False):
    response=np.asarray(result.get('response_db',[]),dtype=float)
    spread=np.asarray(result.get('realization_spread_db',[]),dtype=float)
    noise=np.asarray(result.get('random_noise_standard_error_db',[]),dtype=float)
    deep=response < -40
    limited=(spread>.02)|(noise>.01) if len(response) else np.zeros(0,dtype=bool)
    flags=[]
    if tracked:flags.append('smooth_period_timing_tracking_applied')
    if np.any(deep):flags.append('deep_attenuation_precision_not_certified')
    if np.any(limited):flags.append('noise_or_phase_realization_spread_limits_precision')
    repeatability=np.asarray(result.get('repeatability_std_db',[]),dtype=float)
    result['quality_summary']={
        'local_noise_standard_error_median_db':float(np.median(noise)) if len(noise) else None,
        'local_noise_standard_error_max_db':float(np.max(noise)) if len(noise) else None,
        'reference_1000hz_standard_error_db':result.get('reference_random_noise_standard_error_db'),
        'realization_spread_median_db':float(np.median(spread)) if len(spread) else None,
        'realization_spread_p95_db':float(np.percentile(spread,95)) if len(spread) else None,
        'realization_spread_max_db':float(np.max(spread)) if len(spread) else None,
        'repeatability_std_median_db':float(np.median(repeatability)) if len(repeatability) else None,
        'repeatability_std_max_db':float(np.max(repeatability)) if len(repeatability) else None,
        'interpretation':'finite-repeat local gain noise estimates; 1 kHz normalization error is separate; phase-realization spread includes nonlinear and timing effects; not absolute accuracy, guaranteed confidence intervals, or coherence',
    }
    result['quality_flags']=flags
    result['deep_attenuation_bins']=int(np.count_nonzero(deep))
    result['uncertainty_limited_bins']=int(np.count_nonzero(limited))
    result['quality_warning']=('Deep attenuation below -40 dB and bins with large noise/phase-realization spread need separate uncertainty assessment; these BLA values are not certified to millidecibel accuracy' if np.any(deep)|np.any(limited) else '')
    return result


def analyze_response(wave,fs,meta,level_dbfs=0.,*,max_clock_ppm=.005,cancel=None,
                     allow_period_clock_tracking=False,context_before=None,context_after=None):
    """Session wrapper with explicit rejection/recovery and optional clock warp.

    First refine the constant clock ratio globally from the original capture.
    Only then explicitly enable period tracking with real stage context. A
    single smooth warp must pass both full/half-period final timing checks.
    """
    internal=dict(meta)
    if internal.get('method')!='periodic_dense_bla':raise ValueError('Unsupported response metadata')
    internal['method']=internal.get('generator_method','dense_periodic_multisine_bla_v1')
    reference,expected=generate_dense_response(fs,period_seconds=internal['period_frames']/fs,
        repeats=internal['repeats'],seeds=tuple(internal['phase_seeds']),peak_dbfs=internal['peak_dbfs'],cancel=cancel,
        generator_version=int(internal.get('generator_version',1 if internal['method'].endswith('_v1') else 2)))
    if abs(float(level_dbfs)-internal['peak_dbfs'])>1e-9:raise ValueError('Response level metadata mismatch')
    def invalid(error,tracking=None):
        result={'frequency_hz':[],'response_db':[],'min_db':None,'max_db':None,
            'min_20_20000_db':None,'max_20_20000_db':None,'reference_1000hz_db':None,
            'band_hz':[40.,15000.],'valid':False,'quality':'invalid_clock',
            'reason':str(error),'clock_max_step_ppm':getattr(error,'residual_ppm',None),
            'clock_residual_ppm':getattr(error,'signed_ppm',None),
            'clock_validation_limit_ppm':max_clock_ppm,
            'method':'dense periodic multisine H1/BLA; rejected residual timing error',
            'recovery':'refine global clock alignment from original capture; optionally apply explicitly reported smooth period tracking with real context; final guard must pass'}
        if tracking is not None:result['period_clock_tracking']=tracking
        return result
    tracking=None
    try:
        result=analyze_dense_response(reference,wave,fs,internal,max_clock_ppm=max_clock_ppm,cancel=cancel)
    except DenseResponseClockError as initial:
        if not allow_period_clock_tracking or initial.residual_ppm>.2:return invalid(initial)
        try:
            corrected,tracking=_track_period_clock(reference,wave,fs,internal,context_before,context_after,cancel)
            tracking['initial_clock_max_step_ppm']=initial.residual_ppm
            tracking['initial_clock_residual_ppm']=initial.signed_ppm
            result=analyze_dense_response(reference,corrected,fs,internal,max_clock_ppm=max_clock_ppm,cancel=cancel)
            half_max=_subperiod_clock_max(corrected,fs,internal,cancel)
            tracking['final_half_period_max_step_ppm']=half_max
            if max_clock_ppm is not None and half_max>max_clock_ppm:
                return invalid(DenseResponseClockError(half_max,max_clock_ppm,result['clock_residual_ppm']),tracking)
        except (DenseResponseClockError,ValueError) as error:
            return invalid(error,tracking)
    result['valid']=True
    result['quality']='valid_clock_checked' if max_clock_ppm is not None else 'diagnostic_clock_unchecked'
    if tracking is not None:
        tracking['final_clock_max_step_ppm']=result['clock_max_step_ppm']
        result['period_clock_tracking']=tracking
        result['quality']='valid_after_explicit_period_timing_tracking'
    return _quality_annotations(result,tracked=tracking is not None)
