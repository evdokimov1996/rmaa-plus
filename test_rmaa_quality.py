import unittest
from rmaa_quality import quality_rows


class QualityTests(unittest.TestCase):
    def test_missing_channels_and_nonfinite_metrics_are_not_graded(self):
        result={'left':{'noise':{'noise_a_dbfs_rms':-105},'tone':{'thd_pct':float('nan')}},'right':{}}
        rows=quality_rows(result)
        self.assertEqual(rows[-1][1: ],('Excellent','—'))
        self.assertFalse(any('THD' in row[0] for row in rows))

    def test_overall_uses_worst_measured_metric(self):
        rows=quality_rows({'left':{'tone':{'thd_pct':.0005},'noise':{'noise_a_dbfs_rms':-75}},'right':{}})
        self.assertEqual(rows[-1][1],'Average')
