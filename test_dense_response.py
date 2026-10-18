"""Dense BLA and arbitrary clock interpolation analytical acceptance checks."""
import unittest
import math
import numpy as np
from scipy import signal
from rmaa_dense_response import (generate_dense_response,analyze_dense_response,
    create_response,analyze_response,resample_clock_sinc,DenseResponseClockError)

class TestDenseResponse(unittest.TestCase):
    FS=48000
    def setUp(self):
        self.x,self.meta=generate_dense_response(self.FS)

    def apply_periodic_transfer(self,transfer):
        n=self.meta['period_frames'];r=self.meta['repeats'];y=np.empty_like(self.x,dtype=float)
        frequency=np.fft.rfftfreq(n,1/self.FS)
        for group in range(len(self.meta['phase_seeds'])):
            start=group*r*n
            period=np.fft.irfft(np.fft.rfft(self.x[start:start+n].astype(float))*transfer(frequency),n=n)
            for repeat in range(r):y[start+repeat*n:start+(repeat+1)*n]=period
        return y

    def test_identity_and_zero_dbfs_peak(self):
        self.assertEqual(np.max(abs(self.x)),1.)
        self.assertEqual(len(self.x),16*self.FS)
        result=analyze_dense_response(self.x,self.x,self.FS,self.meta)
        self.assertLess(np.max(np.abs(result['response_db'])),1e-10)
        self.assertEqual(len(result['frequency_hz']),19981)
        self.assertEqual(result['frequency_spacing_hz'],1.)
        self.assertLess(result['clock_max_step_ppm'],.001)
        self.assertNotIn('coherence',result)

    def test_dense_notch_between_old_sparse_points(self):
        def transfer(f):return 1-.999*np.exp(-.5*((f-1024.)/11.)**2)
        y=self.apply_periodic_transfer(transfer)
        r=analyze_dense_response(self.x,y,self.FS,self.meta)
        f=np.asarray(r['frequency_hz']);response=np.asarray(r['response_db'])
        expected=20*np.log10(transfer(f))-20*np.log10(transfer(np.array(1000.)))
        np.testing.assert_allclose(response,expected,atol=1e-8)
        self.assertLess(response[np.argmin(abs(f-1024.))],-59.)

    def test_known_transfer_gain_delay_and_settling(self):
        def transfer(f):return (.75/(1+1j*f/12000.))*np.exp(-2j*np.pi*f*37.25/self.FS)
        y=self.apply_periodic_transfer(transfer)
        # The discarded first period can contain arbitrary startup transients.
        y[:self.FS]=0
        r=analyze_dense_response(self.x,y,self.FS,self.meta)
        f=np.asarray(r['frequency_hz'])
        expected=20*np.log10(abs(transfer(f)))-20*np.log10(abs(transfer(1000.)))
        np.testing.assert_allclose(r['response_db'],expected,atol=1e-9)

    def test_repeat_noise_estimate_and_uncertainty_decreases(self):
        rng=np.random.default_rng(917)
        y=self.x.astype(float)+rng.normal(scale=1e-4,size=len(self.x))
        r=analyze_dense_response(self.x,y,self.FS,self.meta)
        error=np.asarray(r['response_db'])
        self.assertLess(np.sqrt(np.mean(error**2)),.01)
        self.assertGreater(np.mean(r['random_noise_standard_error_db']),0.)
        self.assertGreater(np.mean(r['repeatability_std_db']),np.mean(r['random_noise_standard_error_db']))

    def test_nonlinear_realization_spread_is_not_coherence(self):
        y=self.x.astype(float)+.05*self.x.astype(float)**3
        r=analyze_dense_response(self.x,y,self.FS,self.meta)
        self.assertGreater(np.mean(r['realization_spread_db']),.001)
        self.assertIn('not coherence',r['quality_interpretation'])

    def test_drift_rejected_and_explicit_resampling_recovers(self):
        n=self.FS
        extended=np.r_[self.x[:n],self.x,self.x[-n:]]
        drift=1+10e-6
        capture=resample_clock_sinc(extended,drift,output_frames=len(extended))
        stage=capture[n:n+len(self.x)]
        with self.assertRaises(DenseResponseClockError):
            analyze_dense_response(self.x,stage,self.FS,self.meta)
        diagnostic=analyze_dense_response(self.x,stage,self.FS,self.meta,max_clock_ppm=None)
        self.assertAlmostEqual(diagnostic['clock_residual_ppm'],10.,delta=.05)
        corrected=resample_clock_sinc(capture,1/drift,output_frames=len(capture))
        result=analyze_dense_response(self.x,corrected[n:n+len(self.x)],self.FS,self.meta)
        self.assertLess(np.max(np.abs(result['response_db'])),.002)
        self.assertLess(result['clock_max_step_ppm'],.02)

    def test_session_wrapper_drops_invalid_clock_numeric_results(self):
        x,meta=create_response(self.FS,0.)
        good=analyze_response(x,self.FS,meta,0.)
        self.assertTrue(good['valid'])
        extended=np.r_[x[:self.FS],x,x[-self.FS:]]
        shifted=resample_clock_sinc(extended,1+5e-6,output_frames=len(extended))
        bad=analyze_response(shifted[self.FS:self.FS+len(x)],self.FS,meta,0.)
        self.assertFalse(bad['valid'])
        self.assertEqual(bad['response_db'],[])
        self.assertIsNone(bad['min_db'])

    def test_sinc_passband_ripple_at_44100_48000_384000(self):
        for fs in (44100,48000,384000):
            n=fs//2;t=np.arange(n)/fs;ratio=1+.73e-6
            for f in (20.,1000.,15000.,19000.,20000.):
                with self.subTest(fs=fs,f=f):
                    x=np.sin(2*np.pi*f*t)
                    y=resample_clock_sinc(x,ratio,output_frames=n)
                    valid=np.arange(128,n-128)
                    expected=np.sin(2*np.pi*f*valid*ratio/fs)
                    amplitude=np.dot(y[valid],expected)/np.dot(expected,expected)
                    self.assertLess(abs(20*np.log10(abs(amplitude))),.001)
                    self.assertLess(np.sqrt(np.mean((y[valid]-expected)**2)),1e-4)

    def test_sinc_stereo_and_identity(self):
        t=np.arange(self.FS)/self.FS
        x=np.column_stack((np.sin(2*np.pi*1000*t),np.cos(2*np.pi*2000*t))).astype(np.float32)
        np.testing.assert_array_equal(resample_clock_sinc(x,1.),x)
        y=resample_clock_sinc(x,1+1e-6,output_frames=len(x))
        self.assertEqual(y.shape,x.shape)
        self.assertEqual(y.dtype,np.float32)

    def test_global_ratio_refinement_sign_from_original_capture(self):
        n=self.FS
        extended=np.r_[self.x[:n],self.x,self.x[-n:]]
        true_input_per_output=.999932
        raw=resample_clock_sinc(extended,1/true_input_per_output)
        initial_ratio=true_input_per_output*(1+.15e-6)
        initial=resample_clock_sinc(raw,initial_ratio,output_frames=len(extended))
        diagnostic=analyze_dense_response(self.x,initial[n:n+len(self.x)],self.FS,self.meta,max_clock_ppm=None)
        residual=diagnostic['clock_residual_ppm']
        self.assertAlmostEqual(residual,.15,delta=.001)
        refined_ratio=initial_ratio/(1+residual*1e-6)
        self.assertAlmostEqual(refined_ratio,true_input_per_output,delta=1e-10)
        corrected=resample_clock_sinc(raw,refined_ratio,output_frames=len(extended))
        result=analyze_dense_response(self.x,corrected[n:n+len(self.x)],self.FS,self.meta)
        self.assertLess(np.max(np.abs(result['response_db'])),.002)

    def test_v2_true_peak_and_determinism(self):
        for fs in (44100,48000):
            with self.subTest(fs=fs):
                wave,meta=generate_dense_response(fs)
                self.assertEqual(meta['generator_version'],2)
                n=meta['period_frames']
                for group in range(len(meta['phase_seeds'])):
                    period=wave[group*meta['repeats']*n:group*meta['repeats']*n+n].astype(float)
                    self.assertEqual(abs(period[0]),1.)
                    self.assertEqual(np.max(abs(period)),1.)
                    spectrum=np.fft.rfft(period)
                    if n%2==0:spectrum[-1]*=.5
                    for factor in (8,32):
                        true_peak=float(np.max(abs(np.fft.irfft(spectrum,n=factor*n)*factor)))
                        self.assertLessEqual(true_peak,1+1e-7)
                repeated,other=generate_dense_response(fs)
                np.testing.assert_array_equal(wave,repeated)
                self.assertEqual(meta,other)

    def test_legacy_v1_capture_still_analyzes_from_its_metadata(self):
        x,meta=generate_dense_response(self.FS,generator_version=1)
        meta['generator_method']=meta['method'];meta['method']='periodic_dense_bla'
        result=analyze_response(x,self.FS,meta)
        self.assertTrue(result['valid'])
        self.assertLess(np.max(np.abs(result['response_db'])),1e-10)

    def nonlinear_clock_fixture(self,depth_db=None,noise=0.,abrupt=False):
        # Independent synthesis: finite Fourier derivatives + Taylor time warp.
        # It does not use the production interpolation kernel to make the clock
        # error that the production interpolator is later asked to remove.
        n=self.FS;r=self.meta['repeats'];t=np.arange(len(self.x)+2*n)/self.FS
        if abrupt:
            displacement=.0048*np.abs(t-4.25)
        else:
            displacement=.001604281826*np.sin(2*np.pi*t/7.)
        f=np.fft.rfftfreq(n,1/self.FS)
        transfer=np.ones_like(f) if depth_db is None else 1-(1-10**(depth_db/20))*np.exp(-.5*((f-1024.)/11.)**2)
        derivatives=[]
        for order in range(5):
            derivative=np.empty(len(self.x))
            for group in range(2):
                start=group*r*n
                spectrum=np.fft.rfft(self.x[start:start+n].astype(float))*transfer
                period=np.fft.irfft(spectrum*(2j*np.pi*f/self.FS)**order,n=n)
                derivative[start:start+r*n]=np.tile(period,r)
            derivatives.append(np.r_[derivative[:n],derivative,derivative[-n:]])
        full=sum(derivative*displacement**order/math.factorial(order) for order,derivative in enumerate(derivatives))
        if noise:full+=np.random.default_rng(512).normal(scale=noise,size=len(full))
        meta=dict(self.meta);meta['generator_method']=meta['method'];meta['method']='periodic_dense_bla'
        return full,meta,transfer

    def test_explicit_period_tracking_flat_independent_clock_fixture(self):
        from unittest import mock
        import rmaa_dense_response as dense
        full,meta,_=self.nonlinear_clock_fixture()
        n=self.FS;stage=full[n:n+len(self.x)]
        sizes=[];original=dense._resample_position_blocks
        def bounded(x,frames,position_function,**kwargs):
            def observed(start,stop):
                sizes.append(stop-start)
                return position_function(start,stop)
            return original(x,frames,observed,**kwargs)
        with mock.patch.object(dense,'_resample_position_blocks',side_effect=bounded):
            result=analyze_response(stage,self.FS,meta,allow_period_clock_tracking=True,
                context_before=full[n-128:n],context_after=full[n+len(self.x):n+len(self.x)+128])
        self.assertTrue(result['valid'],result)
        self.assertEqual(result['quality'],'valid_after_explicit_period_timing_tracking')
        self.assertLess(np.max(np.abs(result['response_db'])),.001)
        self.assertLessEqual(max(sizes),2048)
        tracking=result['period_clock_tracking']
        self.assertEqual(tracking['correction_passes'],1)
        self.assertLess(tracking['final_half_period_max_step_ppm'],.005)
        self.assertIn('stable',tracking['assumption'])
        self.assertIn('magnitude only',tracking['scope'])
        self.assertIn('not absolute accuracy',result['quality_summary']['interpretation'])

    def test_period_tracking_known_notch_and_deep_noise_quality(self):
        n=self.FS
        for depth,noise in ((-35.,0.),(-60.,1e-6)):
            with self.subTest(depth_db=depth):
                full,meta,transfer=self.nonlinear_clock_fixture(depth,noise)
                result=analyze_response(full[n:n+len(self.x)],self.FS,meta,allow_period_clock_tracking=True,
                    context_before=full[n-128:n],context_after=full[n+len(self.x):n+len(self.x)+128])
                self.assertTrue(result['valid'],result)
                f=np.asarray(result['frequency_hz']);expected=20*np.log10(transfer[f.astype(int)])
                expected-=expected[np.where(f==1000)[0][0]]
                error=np.asarray(result['response_db'])-expected
                if depth>-40:self.assertLess(np.max(abs(error)),.01)
                else:
                    self.assertGreater(result['deep_attenuation_bins'],0)
                    self.assertIn('deep_attenuation_precision_not_certified',result['quality_flags'])
                    self.assertTrue(result['quality_warning'])

    def test_abrupt_clock_change_and_sample_slip_rejected(self):
        n=self.FS
        full,meta,_=self.nonlinear_clock_fixture(abrupt=True)
        result=analyze_response(full[n:n+len(self.x)],self.FS,meta,allow_period_clock_tracking=True,
            context_before=full[n-128:n],context_after=full[n+len(self.x):n+len(self.x)+128])
        self.assertFalse(result['valid'])
        original=np.r_[self.x[:n],self.x,self.x[-n:]].astype(float)
        slip=original.copy();location=round(5.25*n);slip[location:-1]=original[location+1:]
        result=analyze_response(slip[n:n+len(self.x)],self.FS,meta,allow_period_clock_tracking=True,
            context_before=slip[n-128:n],context_after=slip[n+len(self.x):n+len(self.x)+128])
        self.assertFalse(result['valid'])
        self.assertGreater(result['clock_max_step_ppm'],.2)

    def test_tracking_needs_real_context_and_respects_cancellation(self):
        import threading
        from rmaa_transport import MeasurementCancelled
        n=self.FS;full,meta,_=self.nonlinear_clock_fixture()
        result=analyze_response(full[n:n+len(self.x)],self.FS,meta,allow_period_clock_tracking=True)
        self.assertFalse(result['valid'])
        self.assertIn('context',result['reason'])
        cancel=threading.Event();cancel.set()
        with self.assertRaises(MeasurementCancelled):
            analyze_response(self.x,self.FS,meta,cancel=cancel,allow_period_clock_tracking=True)

    def test_invalid_settings_and_stage_length(self):
        with self.assertRaises(ValueError):generate_dense_response(self.FS,seeds=(1,1))
        with self.assertRaises(ValueError):generate_dense_response(self.FS,peak_dbfs=.1)
        with self.assertRaises(ValueError):analyze_dense_response(self.x,self.x[:-1],self.FS,self.meta)

if __name__=='__main__':unittest.main(verbosity=2)
