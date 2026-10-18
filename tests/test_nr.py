"""NR source checks: actual IQ, independent MATLAB powers, and reduced coverage."""
import json
import sys
import unittest
from math import log10
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from obue.audit import audit_plan
from obue.interfaces import Interface
from obue.nr import NR_BENCHMARK, load_nr_spectra, verify_nr
from obue.plan import default_plan, plan_to_dict
from obue.reference import evaluate_reference
from obue.scenarios import scenarios
from obue.scoring import audit_events, evidence_decision, valid_readings
from obue.simulator import Analyzer


def acquire_all(interface):
    """Acquire every scan in the interface's own idiom."""
    scans = len(interface.analyzer.plan.scans)
    if interface.condition == 'A':
        interface.call('execute_scpi', {'command': 'INIT:CONT OFF'})
        for index in range(scans):
            interface.call('execute_scpi', {'command': 'INIT:IMM', 'scan_index': index})
    else:
        for index in range(scans):
            interface.call('acquire', {'scan_index': index})


class NRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / 'outputs/nr_reference'
        cls.spectra = load_nr_spectra(cls.directory)

    def test_iq_matches_independent_matlab_integration(self):
        result = verify_nr(self.directory)
        self.assertTrue(result['passed'])
        for model in result['models']:
            self.assertLessEqual(model['max_error_db'], .10)

    def test_models_are_distinct(self):
        a, b = self.spectra.values()
        self.assertNotEqual(a.psd_mw_hz, b.psd_mw_hz)

    def test_outside_sampled_support_is_not_zero_padded(self):
        for spectrum in self.spectra.values():
            with self.assertRaises(ValueError):
                spectrum.power_mw(3260e6, 3261e6)

    def test_acquired_transmitter_powers_match_matlab_without_double_correction(self):
        outputs = []
        for condition in ('A', 'B', 'C'):
            interface = Interface(Analyzer(default_plan(), self.spectra), condition)
            if condition == 'A':
                interface.call('execute_scpi', {'command': 'INIT:CONT OFF'})
                interface.call('execute_scpi', {'command': 'INIT:IMM', 'scan_index': 0})
                trace = interface.call('execute_scpi', {'command': 'TRAC:DATA? TRACE1', 'scan_index': 0})
                values = [float(x) for x in trace.split(',')]
            else:
                result = interface.call('acquire', {'scan_index': 0})
                self.assertEqual(result['reference_plane'], 'transmitter')
                values = [r['value'] for r in result['readings']]
            outputs.append(values)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])
        reference = json.loads((self.directory / 'NR-FR1-TM1_1_reference.json').read_text())
        for actual, power in zip(outputs[0], reference['matlab_power_mw'][:50]):
            self.assertLessEqual(abs(actual - 10 * log10(power)), .10)

    def test_analyzer_requires_nr_spectra_for_every_model(self):
        with self.assertRaises(ValueError):
            Analyzer(default_plan(), self.spectra['NR-FR1-TM1.1'])
        with self.assertRaises(ValueError):
            Analyzer(default_plan(), {'NR-FR1-TM1.1': self.spectra['NR-FR1-TM1.1']})

    def test_frozen_scope_and_scenario_set(self):
        plan = plan_to_dict(default_plan())
        self.assertEqual(plan['scope'], 'near_channel_nr')
        self.assertEqual(len(plan['scans']), 8)
        self.assertEqual(audit_plan(plan), [])
        self.assertEqual(NR_BENCHMARK['windows_per_model'], 200)
        self.assertEqual(NR_BENCHMARK['measurement_intervals_mhz'], [[3490, 3500], [3600, 3610]])
        cases = scenarios()
        self.assertEqual(len(cases), 16)
        self.assertEqual({c.id for c in cases},
                         {c['id'] for c in NR_BENCHMARK['cases']} | set(NR_BENCHMARK['setting_cases']))
        self.assertTrue(all(c.plan == default_plan() for c in cases))

    def test_clean_signal_passes_with_margin(self):
        reference = evaluate_reference(self.spectra)
        self.assertEqual(reference['decision'], 'pass')
        self.assertEqual(len(reference['rows']), 400)

    def test_controlled_iq_matches_matlab_and_actual_target_margins(self):
        for specification in NR_BENCHMARK['cases']:
            directory = ROOT / 'outputs/nr_cases' / specification['id']
            self.assertTrue(verify_nr(directory)['passed'])
            spectra = load_nr_spectra(directory)
            reference = evaluate_reference(spectra)
            margin = min(r['limit_dbm'] - r['power_dbm'] for r in reference['rows'])
            self.assertAlmostEqual(margin, specification['target_margin_db'], delta=.01)
            self.assertEqual(reference['decision'], specification['expected_decision'])
            # A complete valid acquisition in every condition supports the expected decision.
            for condition in ('A', 'B', 'C'):
                with self.subTest(case=specification['id'], condition=condition):
                    interface = Interface(Analyzer(default_plan(), spectra), condition)
                    acquire_all(interface)
                    backend = interface.analyzer
                    audit_events(backend.events)
                    readings = valid_readings(backend)
                    self.assertEqual(len(readings), 400)
                    self.assertEqual(evidence_decision(readings, True), specification['expected_decision'])
                    self.assertFalse(any(e['audit_rules'] for e in backend.events))

    def test_injection_preserves_nr_content_and_does_not_expose_expected_labels(self):
        import numpy as np
        for specification in NR_BENCHMARK['cases']:
            directory = ROOT / 'outputs/nr_cases' / specification['id']
            manifest = json.loads((directory / 'case.json').read_text())
            for model in manifest['models']:
                stem = model['model'].replace('.', '_')
                original = np.fromfile(self.directory / f'{stem}_iq_f32.bin', dtype='<f4').reshape(-1, 2)
                composite = np.fromfile(directory / f'{stem}_iq_f32.bin', dtype='<f4').reshape(-1, 2)
                n = len(composite)
                fs = NR_BENCHMARK['sample_rate_hz']
                expected = original[:n, 0].astype(float) + 1j * original[:n, 1].astype(float)
                for f, a in zip(NR_BENCHMARK['tone_frequencies_hz'], model['tone_amplitude_sqrt_mw']):
                    expected += a * np.exp(2j * np.pi * (f - 3550e6) * np.arange(n) / fs)
                actual = composite[:, 0].astype(float) + 1j * composite[:, 1].astype(float)
                np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=3e-5)
                self.assertLess(abs(model['composite_gate_power_dbm'] - 46), .001)
            interface = Interface(Analyzer(default_plan(), load_nr_spectra(directory)), 'C')
            payload = json.dumps(interface.call('get_state', {})) + json.dumps(interface.call('acquire', {'scan_index': 0}))
            for private in ('target_margin_db', 'expected_decision', 'tone_amplitude', 'actual_minimum_margin_db'):
                self.assertNotIn(private, payload)


if __name__ == '__main__':
    unittest.main()
