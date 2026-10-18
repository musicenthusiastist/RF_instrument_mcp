import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import unittest
from dataclasses import replace
from obue.domain import AveragingDomain, Detector, Quantity, Unit, decision_for_upper_limit
from obue.plan import default_plan, plan_from_dict, plan_to_dict, replace_scan
from obue.validation import validate_plan


class PlanTests(unittest.TestCase):
    def setUp(self):self.plan=default_plan()
    def rules(self,p):return {v.requirement for v in validate_plan(p)}
    def state(self,**kwargs):return replace_scan(self.plan,0,analyzer=replace(self.plan.scans[0].analyzer,**kwargs))

    def test_valid_and_roundtrip(self):
        self.assertEqual(validate_plan(self.plan),[])
        self.assertEqual(plan_from_dict(plan_to_dict(self.plan)),self.plan)

    def test_equivalent_units(self):
        self.assertEqual(validate_plan(self.state(rbw=Quantity(100,Unit.KHZ))),[])

    def test_wrong_dimension(self):self.assertIn('IFACE-001',self.rules(self.state(rbw=Quantity(100,Unit.DBM))))
    def test_rbw_larger(self):self.assertIn('PROC-001',self.rules(self.state(rbw=Quantity(1,Unit.MHZ))))
    def test_rbw_smaller_is_invalid(self):
        self.assertIn('PROC-001',self.rules(self.state(rbw=Quantity(10,Unit.KHZ))))

    def test_wrong_detector_and_average(self):
        for changes in ({'detector':Detector.PEAK},{'averaging_domain':AveragingDomain.LOG}):
            with self.subTest(changes=changes):self.assertIn('PROC-002',self.rules(self.state(**changes)))

    def test_missing_duplicate_and_wrong_width(self):
        for p in (replace(self.plan,scans=self.plan.scans[1:]),replace(self.plan,scans=self.plan.scans+(self.plan.scans[0],)),
                  replace_scan(self.plan,0,region=replace(self.plan.scans[0].region,measurement_bandwidth=Quantity(1,Unit.MHZ)))):
            self.assertIn('PROC-004',self.rules(p))

    def test_missing_test_model(self):self.assertIn('PROC-005',self.rules(replace(self.plan,scans=self.plan.scans[:4])))
    def test_nr_grid_and_fixed_loss(self):
        self.assertEqual(len(self.plan.scans),8)
        self.assertIn('SAFE-001',self.rules(replace(self.plan,external_loss_db=41)))
    def test_unknown_scope(self):self.assertIn('SCOPE-003',self.rules(replace(self.plan,scope='full_analytical')))

    def test_overload(self):
        for p in (self.state(preamp_enabled=True),self.state(attenuation=Quantity(0,Unit.DB))):
            self.assertIn('SAFE-001',self.rules(p))

    def test_span_and_rated_power(self):
        self.assertIn('PROC-004',self.rules(self.state(span=Quantity(100,Unit.MHZ))))
        self.assertIn('PROC-003',self.rules(replace(self.plan,actual_output_power_dbm=40)))

    def test_geometry_does_not_iterate_unbounded(self):
        r=replace(self.plan.scans[0].region,step=Quantity(1,Unit.HZ))
        self.assertIn('PROC-004',self.rules(replace_scan(self.plan,0,region=r)))

    def test_decision_boundary_and_nonfinite(self):
        self.assertEqual(decision_for_upper_limit(-14,-14).value,'pass')
        self.assertEqual(decision_for_upper_limit(-13.999,-14).value,'fail')
        for x in (float('nan'),float('inf'),-float('inf')):
            for args in ((x,-14),(-15,x)):
                with self.assertRaises(ValueError):decision_for_upper_limit(*args)


if __name__=='__main__':unittest.main()
