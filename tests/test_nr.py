"""NR source checks: actual IQ, independent MATLAB powers, and reduced coverage."""
import json
import sys
import unittest
from dataclasses import replace
from math import log10
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from obue.nr import load_nr_spectra,verify_nr,NR_BENCHMARK,nr_scenarios
from obue.plan import default_plan,plan_to_dict
from obue.simulator import Analyzer
from obue.interfaces import Interface
from obue.benchmark import run_session
from obue.scenarios import scenarios
from obue.audit import audit_plan


class NRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=ROOT/'outputs/nr_reference'
        cls.spectra=load_nr_spectra(cls.directory)

    def test_iq_matches_independent_matlab_integration(self):
        result=verify_nr(self.directory)
        self.assertTrue(result['passed'])
        for model in result['models']:self.assertLessEqual(model['max_error_db'],.10)

    def test_models_are_distinct(self):
        a,b=self.spectra.values()
        self.assertNotEqual(a.psd_mw_hz,b.psd_mw_hz)

    def test_outside_sampled_support_is_not_zero_padded(self):
        for spectrum in self.spectra.values():
            with self.assertRaises(ValueError):spectrum.power_mw(3260e6,3261e6)

    def test_acquired_transmitter_powers_match_matlab_without_double_correction(self):
        plan=default_plan('near_channel_nr')
        outputs=[]
        for condition in ('A','B','C'):
            analyzer=Analyzer(plan,self.spectra);interface=Interface(analyzer,condition)
            if condition=='A':
                interface.call('execute_scpi',{'command':'INIT:CONT OFF'})
                interface.call('execute_scpi',{'command':'INIT:IMM','scan_index':0})
                values=[float(x) for x in interface.call('execute_scpi',{'command':'TRAC:DATA? TRACE1','scan_index':0}).split(',')]
            else:
                result=interface.call('acquire',{'scan_index':0})
                self.assertEqual(result['reference_plane'],'transmitter')
                values=[r['value'] for r in result['readings']]
            outputs.append(values)
        self.assertEqual(outputs[0],outputs[1]);self.assertEqual(outputs[1],outputs[2])
        reference=json.loads((self.directory/'NR-FR1-TM1_1_reference.json').read_text())
        for actual,power in zip(outputs[0],reference['matlab_power_mw'][:50]):
            self.assertLessEqual(abs(actual-10*log10(power)),.10)

    def test_complete_reduced_grid_is_not_claimed_as_full_obue(self):
        plan=default_plan('near_channel_nr')
        self.assertEqual(audit_plan(plan_to_dict(plan)),[])
        case=replace(scenarios()[0],plan=plan)
        result=run_session(case,'C',0,spectrum=self.spectra)
        self.assertEqual(result['scope'],'near_channel_nr')
        self.assertEqual(result['measurement_rows'],400)
        self.assertTrue(result['eligible_complete'])
        self.assertFalse(result['decision_error'])
        self.assertEqual(result['invalid_executions'],0)
        self.assertNotIn('seed',result)

    def test_full_plan_cannot_use_reduced_nr_source(self):
        with self.assertRaises(ValueError):Analyzer(default_plan(),self.spectra)

    def test_frozen_scope_and_scenario_set(self):
        plan=plan_to_dict(default_plan('near_channel_nr'))
        self.assertEqual(len(plan['scans']),8)
        self.assertEqual(NR_BENCHMARK['windows_per_model'],200)
        self.assertEqual(NR_BENCHMARK['measurement_intervals_mhz'],[[3490,3500],[3600,3610]])
        cases=nr_scenarios()
        self.assertEqual(len(cases),16)
        self.assertEqual({c.id for c in cases},{c['id'] for c in NR_BENCHMARK['cases']}|set(NR_BENCHMARK['setting_cases']))

    def test_controlled_iq_matches_matlab_and_actual_target_margins(self):
        from obue.reference import evaluate_reference
        for specification in NR_BENCHMARK['cases']:
            directory=ROOT/'outputs/nr_cases'/specification['id']
            self.assertTrue(verify_nr(directory)['passed'])
            spectra=load_nr_spectra(directory)
            reference=evaluate_reference(spectra,default_plan('near_channel_nr'))
            margin=min(r['limit_dbm']-r['power_dbm'] for r in reference['rows'])
            self.assertAlmostEqual(margin,specification['target_margin_db'],delta=.01)
            self.assertEqual(reference['decision'],specification['expected_decision'])
            case=next(c for c in nr_scenarios() if c.id==specification['id'])
            for condition in ('A','B','C'):
                result=run_session(case,condition,0,spectrum=spectra)
                self.assertTrue(result['eligible_complete'])
                self.assertFalse(result['decision_error'])
                self.assertEqual(result['reported_decision'],specification['expected_decision'])
                self.assertEqual(result['measurement_rows'],400)
                self.assertEqual(result['false_blocks'],0)

    def test_injection_preserves_nr_content_and_does_not_expose_expected_labels(self):
        import numpy as np
        for specification in NR_BENCHMARK['cases']:
            directory=ROOT/'outputs/nr_cases'/specification['id']
            manifest=json.loads((directory/'case.json').read_text())
            for model in manifest['models']:
                stem=model['model'].replace('.','_')
                original=np.fromfile(self.directory/f'{stem}_iq_f32.bin',dtype='<f4').reshape(-1,2)
                composite=np.fromfile(directory/f'{stem}_iq_f32.bin',dtype='<f4').reshape(-1,2)
                n=len(composite);fs=NR_BENCHMARK['sample_rate_hz']
                expected=original[:n,0].astype(float)+1j*original[:n,1].astype(float)
                for f,a in zip(NR_BENCHMARK['tone_frequencies_hz'],model['tone_amplitude_sqrt_mw']):
                    expected+=a*np.exp(2j*np.pi*(f-3550e6)*np.arange(n)/fs)
                actual=composite[:,0].astype(float)+1j*composite[:,1].astype(float)
                np.testing.assert_allclose(actual,expected,rtol=1e-6,atol=3e-5)
                self.assertLess(abs(model['composite_gate_power_dbm']-46),.001)
            analyzer=Analyzer(default_plan('near_channel_nr'),load_nr_spectra(directory))
            payload=json.dumps(Interface(analyzer,'C').call('get_state',{}))+json.dumps(Interface(analyzer,'C').call('acquire',{'scan_index':0}))
            for private in ('target_margin_db','expected_decision','tone_amplitude','actual_minimum_margin_db'):
                self.assertNotIn(private,payload)


if __name__=='__main__':unittest.main()
