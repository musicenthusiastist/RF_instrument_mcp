import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from obue.domain import (  # noqa: E402
    ConformityDecision,
    Quantity,
    Unit,
    decision_for_upper_limit,
    expected_obue_offsets_hz,
    frozen_dut_state,
    obue_limit_dbm,
    obue_measurement_bandwidth_hz,
)


class QuantityTests(unittest.TestCase):
    def test_frequency_conversion(self):
        self.assertEqual(Quantity(100.0, Unit.MHZ).to(Unit.HZ).value, 100e6)

    def test_dimension_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            Quantity(100.0, Unit.MHZ).to(Unit.DBM)


class FrozenProfileTests(unittest.TestCase):
    def test_channel_edges(self):
        dut = frozen_dut_state()
        self.assertEqual(dut.channel_lower_edge_hz, 3.5e9)
        self.assertEqual(dut.channel_upper_edge_hz, 3.6e9)

    def test_expected_offset_grid_is_contiguous_at_filter_boundaries(self):
        offsets = expected_obue_offsets_hz()
        self.assertEqual(offsets[0], 50_000)
        self.assertIn(4_950_000, offsets)
        self.assertIn(5_050_000, offsets)
        self.assertIn(9_950_000, offsets)
        self.assertIn(10_500_000, offsets)
        self.assertEqual(offsets[-1], 239_500_000)
        self.assertEqual(len(offsets), 330)

    def test_machine_readable_rules_match_domain_model(self):
        rules_path = PROJECT_ROOT / "requirements" / "obue_rules.json"
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        profile = rules["dut_profile"]
        geometry = rules["derived_geometry"]
        regions = rules["obue_mask"]["regions"]

        self.assertEqual(profile["band"], "n78")
        self.assertEqual(profile["carrier_center_hz"], 3_550_000_000)
        self.assertEqual(geometry["f_offset_max_hz_each_side"], 240_000_000)
        self.assertEqual(regions[0]["centre_offset_start_hz"], 50_000)
        self.assertEqual(regions[2]["measurement_bandwidth_hz"], 1_000_000)


class ObueMaskTests(unittest.TestCase):
    def test_sloped_region(self):
        self.assertAlmostEqual(obue_limit_dbm(50_000), -7.0)
        self.assertAlmostEqual(obue_limit_dbm(2_550_000), -10.5)
        self.assertAlmostEqual(obue_limit_dbm(4_950_000), -13.86)

    def test_flat_regions_and_bandwidths(self):
        self.assertEqual(obue_limit_dbm(5_050_000), -14.0)
        self.assertEqual(obue_measurement_bandwidth_hz(5_050_000), 100e3)
        self.assertEqual(obue_limit_dbm(10_500_000), -13.0)
        self.assertEqual(obue_measurement_bandwidth_hz(10_500_000), 1e6)

    def test_invalid_transition_centre_is_rejected(self):
        with self.assertRaises(ValueError):
            obue_limit_dbm(10_250_000)


class DecisionTests(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(
            decision_for_upper_limit(-16.0, -14.0),
            ConformityDecision.PASS,
        )

    def test_equality_passes(self):
        self.assertEqual(
            decision_for_upper_limit(-14.0, -14.0),
            ConformityDecision.PASS,
        )

    def test_fail(self):
        self.assertEqual(
            decision_for_upper_limit(-12.0, -14.0),
            ConformityDecision.FAIL,
        )

    def test_nonfinite_is_rejected(self):
        with self.assertRaises(ValueError):
            decision_for_upper_limit(float('nan'), -14.0)


if __name__ == "__main__":
    unittest.main()
