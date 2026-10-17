import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import unittest
import random
from obue.spectrum import Spectrum,make_obue_fixture,dbm_to_mw,mw_to_dbm
from obue.reference import reference_power_mw,reference_limit,evaluate_reference
from obue.domain import expected_obue_offsets_hz,obue_limit_dbm,obue_measurement_bandwidth_hz


class SpectrumTests(unittest.TestCase):
    def test_flat_noise_analytical(self):
        s=Spectrum((0,1e6),(1e-12,))
        self.assertAlmostEqual(s.power_mw(0,1e5),1e-7,places=16)
        self.assertAlmostEqual(mw_to_dbm(s.power_mw(0,1e5)),-70)

    def test_partial_overlap_and_tone_boundary(self):
        s=Spectrum((0,10,20),(1,2),((10,3),(20-1e-6,4)))
        self.assertEqual(s.power_mw(5,15),18)
        self.assertEqual(s.power_mw(0,10),10)
        self.assertAlmostEqual(s.power_mw(10,20),27)

    def test_independent_reference_random_windows(self):
        s=Spectrum((0,10,20,30),(1,.2,3),((9,4),(20,2)))
        rng=random.Random(39)
        for _ in range(100):
            a,b=sorted([rng.uniform(0,30),rng.uniform(0,30)])
            self.assertAlmostEqual(s.power_mw(a,b),reference_power_mw(s,a,b),places=12)

    def test_full_filter_grid_contiguity(self):
        offsets=expected_obue_offsets_hz();bounds=[(o-obue_measurement_bandwidth_hz(o)/2,o+obue_measurement_bandwidth_hz(o)/2) for o in offsets]
        self.assertEqual(bounds[0][0],0);self.assertEqual(bounds[-1][1],240e6)
        self.assertTrue(all(a[1]==b[0] for a,b in zip(bounds,bounds[1:])))

    def test_all_mask_values_against_separate_reference(self):
        for off in expected_obue_offsets_hz():self.assertAlmostEqual(obue_limit_dbm(off),reference_limit(off),places=12)

    def test_all_json_regions_match_production_grid_and_mask(self):
        from obue.config import RULES
        from math import isclose
        grid=[]
        for r in RULES['obue_mask']['regions']:
            for off in range(r['centre_offset_start_hz'],r['centre_offset_stop_exclusive_hz'],r['centre_step_hz']):
                grid.append(off)
                self.assertEqual(obue_measurement_bandwidth_hz(off),r['measurement_bandwidth_hz'])
                if r['limit']['kind']=='constant_dbm':self.assertEqual(obue_limit_dbm(off),r['limit']['value'])
                else:
                    # Restricted documented expression; never execute the JSON string.
                    self.assertEqual(r['limit']['expression'],'-7.0 - 1.4 * (f_offset_mhz - 0.05)')
                    self.assertTrue(isclose(obue_limit_dbm(off),reference_limit(off),abs_tol=1e-12))
        self.assertEqual(tuple(grid),expected_obue_offsets_hz())

    def test_every_fixture_bin_has_known_power(self):
        s=make_obue_fixture(3)
        self.assertAlmostEqual(mw_to_dbm(s.power_mw(3500e6,3600e6)),46)
        for side in ('lower','upper'):
            for off in expected_obue_offsets_hz():
                f=3500e6-off if side=='lower' else 3600e6+off;bw=obue_measurement_bandwidth_hz(off)
                self.assertAlmostEqual(mw_to_dbm(s.power_mw(f-bw/2,f+bw/2)),reference_limit(off)-3,places=10)

    def test_reference_pass_fail(self):
        for margin,expected in [(3,'pass'),(-3,'fail'),(.2,'pass'),(-.2,'fail')]:
            self.assertEqual(evaluate_reference(make_obue_fixture(margin))['decision'],expected)

    def test_invalid_psd_and_windows(self):
        for edges,density in [((0,0),(1,)),((0,1),(-1,)),((0,1),(1,2))]:
            with self.assertRaises(ValueError):Spectrum(edges,density)
        s=Spectrum((0,1),(1,))
        for lo,hi in [(-1,1),(0,2),(1,0),(0,float('nan'))]:
            with self.assertRaises(ValueError):s.power_mw(lo,hi)


if __name__=='__main__':unittest.main()
