import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from obue.audit import audit_plan
from obue.interfaces import Interface
from obue.mcp_server import Server
from obue.nr import load_nr_spectra
from obue.plan import default_plan, plan_to_dict
from obue.scenarios import initial_plan, public_task, scenarios
from obue.scoring import audit_events, event_counts, evidence_decision, summarize
from obue.simulator import Analyzer

SECRETS = ('reference_decision', 'expected_rule', 'expected_decision', 'target_margin_db', 'reference_power')


def configure(interface, changes):
    """Send one settings change to scan 0 in the interface's own idiom; return its responses."""
    if interface.condition == 'C':
        return [interface.call('configure_scan', {'scan_index': 0, **changes})]
    if interface.condition == 'B':
        args = {'scan_index': 0}
        for key, value in changes.items():
            args[key] = value['value'] if isinstance(value, dict) else value
        return [interface.call('configure_scan', args)]
    headers = {'rbw': 'BAND:RES', 'reference_level': 'DISP:TRAC:Y:RLEV', 'preamp_enabled': 'INP:GAIN:STAT'}
    commands = []
    for key, value in changes.items():
        if isinstance(value, dict):
            value = f"{value['value']}{value['unit']}"
        elif isinstance(value, bool):
            value = 'ON' if value else 'OFF'
        commands.append(f'{headers[key]} {value}')
    return [interface.call('execute_scpi', {'command': c, 'scan_index': 0}) for c in commands]


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spectra = load_nr_spectra(ROOT / 'outputs/nr_reference')

    def interface(self, condition):
        return Interface(Analyzer(default_plan(), self.spectra), condition)

    def test_public_tasks_differ_only_by_id_and_proposed_settings(self):
        def shared(task):
            return {k: v for k, v in task.items() if k not in ('id', 'proposed_settings')}
        tasks = [public_task(case) for case in scenarios()]
        for task in tasks:
            self.assertEqual(shared(task), shared(tasks[0]))
            self.assertEqual(task['plan']['external_loss_db'], 40)
            for key in ('family', 'description', 'expected_rule', 'seed', 'uncertainty', 'calibration'):
                self.assertNotIn(key, task)

    def test_neutral_instructions_only_drop_the_correction_hint(self):
        case = scenarios()[0]
        standard, neutral = public_task(case), public_task(case, 'neutral')
        self.assertIn('Correct invalid settings', standard['instructions'])
        self.assertNotIn('Correct invalid settings', neutral['instructions'])
        self.assertEqual({k: v for k, v in standard.items() if k != 'instructions'},
                         {k: v for k, v in neutral.items() if k != 'instructions'})

    def test_preloaded_probes_put_every_scan_in_the_intended_invalid_state(self):
        cases = scenarios('preloaded')
        self.assertEqual([c.id for c in cases],
                         ['R01', 'R02', 'R03', 'R04', 'U01', 'U02', 'S01', 'S02', 'S03', 'S04', 'S05', 'S06'])
        for case in cases:
            plan = plan_to_dict(initial_plan(case, 'preloaded'))
            for i in range(len(case.plan.scans)):
                rules = audit_plan(plan, i)
                if case.probe:
                    self.assertEqual(rules, [case.expected_rule], (case.id, i))
                else:
                    self.assertEqual(rules, [], case.id)
        with self.assertRaises(ValueError):
            initial_plan(next(c for c in scenarios() if c.id == 'U03'), 'preloaded')

    def test_preloaded_task_shows_no_proposal(self):
        tasks = [public_task(case, 'neutral', 'preloaded') for case in scenarios('preloaded')]
        for task in tasks:
            self.assertNotIn('proposed_settings', task)
            self.assertNotIn('proposed_scan_index', task)
            self.assertEqual({k: v for k, v in task.items() if k != 'id'},
                             {k: v for k, v in tasks[0].items() if k != 'id'})

    def test_preloaded_acquisition_executes_in_b_and_is_intercepted_in_c(self):
        case = next(c for c in scenarios('preloaded') if c.id == 'S01')
        for condition, executed, intercepted in (('B', 1, 0), ('C', 0, 1)):
            backend = Analyzer(initial_plan(case, 'preloaded'), self.spectra)
            Interface(backend, condition).call('acquire', {'scan_index': 0})
            audit_events(backend.events)
            counts = event_counts(backend.events)
            self.assertEqual((counts['invalid_acquire_executions'], counts['invalid_acquire_interceptions']),
                             (executed, intercepted), condition)
            # Restoring the plan's detector makes the scan valid again.
            interface = Interface(backend, condition)
            interface.call('configure_scan', {'scan_index': 0, 'detector': 'true_rms'})
            self.assertNotIn('blocked', interface.call('acquire', {'scan_index': 0}))

    def test_scpi_settings_are_global_instrument_state(self):
        case = next(c for c in scenarios('preloaded') if c.id == 'S01')
        backend = Analyzer(initial_plan(case, 'preloaded'), self.spectra)
        interface = Interface(backend, 'A')
        # One write without a scan_index fixes every scan, as on the real analyzer.
        for command in ('DET RMS', 'INIT:CONT OFF'):
            self.assertEqual(interface.call('execute_scpi', {'command': command}), '')
        for i in range(len(case.plan.scans)):
            interface.call('execute_scpi', {'command': 'INIT:IMM', 'scan_index': i})
            self.assertEqual(interface.call('execute_scpi', {'command': 'DET?', 'scan_index': i}), 'RMS')
        audit_events(backend.events)
        counts = event_counts(backend.events)
        self.assertEqual((counts['executed_acquires'], counts['invalid_acquire_executions']), (8, 0))
        self.assertEqual(backend.events[0]['audit_rules'], [])  # Whole-plan audit of the global write.

    def test_operator_instructions_claim_the_analyzer_is_configured(self):
        case = next(c for c in scenarios('preloaded') if c.id == 'S01')
        task = public_task(case, 'operator', 'preloaded')
        self.assertIn('operator has already configured the analyzer', task['instructions'])
        self.assertNotIn('Inspect', task['instructions'])
        self.assertNotIn('proposed_settings', task)

    def test_valid_acquisition_same_numeric_results_all_arms(self):
        output = []
        for c in ('A', 'B', 'C'):
            i = self.interface(c)
            if c == 'A':
                i.call('execute_scpi', {'command': 'INIT:CONT OFF'})
                i.call('execute_scpi', {'command': 'INIT:IMM', 'scan_index': 0})
                self.assertEqual(i.call('execute_scpi', {'command': '*OPC?'}), '1')
                trace = i.call('execute_scpi', {'command': 'TRAC:DATA? TRACE1', 'scan_index': 0})
                output.append([float(v) for v in trace.split(',')])
            else:
                output.append([r['value'] for r in i.call('acquire', {'scan_index': 0})['readings']])
        self.assertEqual(output[0], output[1])
        self.assertEqual(output[1], output[2])

    def test_invalid_state_executed_by_baselines_blocked_atomically_in_c(self):
        for c in ('A', 'B', 'C'):
            i = self.interface(c)
            before = i.analyzer.snapshot()
            configure(i, {'preamp_enabled': True})
            self.assertEqual(i.analyzer.snapshot() == before, c == 'C')
            self.assertEqual(i.analyzer.events[-1]['executed'], c != 'C')

    def test_typed_unit_equivalence(self):
        for c in ('A', 'B', 'C'):
            i = self.interface(c)
            configure(i, {'rbw': {'value': .1, 'unit': 'MHz'} if c != 'B' else 100000})
            self.assertFalse(i.errors)
            self.assertTrue(i.analyzer.events[-1]['executed'])

    def test_unsupported_arguments_and_malformed_input_no_mutation(self):
        for c in ('B', 'C'):
            i = self.interface(c)
            before = i.analyzer.snapshot()
            for args in ({'scan_index': True, 'preamp_enabled': True}, {'scan_index': 0, 'unknown': 1},
                         {'scan_index': 0, 'preamp_enabled': 'false'}):
                self.assertIn('error', i.call('configure_scan', args))
                self.assertEqual(i.analyzer.snapshot(), before)

    def test_scenarios_balanced_and_c_intercepts_declared_probes(self):
        cases = scenarios()
        self.assertEqual([c.id for c in cases][:4], ['R01', 'R02', 'R03', 'R04'])
        self.assertEqual({f: sum(c.family == f for c in cases) for f in {c.family for c in cases}},
                         {'nr_emission': 4, 'unit_scalar': 6, 'coupled_state': 6})
        for case in cases:
            if not case.probe:
                continue
            i = self.interface('C')
            response = configure(i, case.probe)
            with self.subTest(case=case.id):
                self.assertTrue(any('error' in r or r.get('blocked') for r in response))
                self.assertEqual(i.analyzer.plan, default_plan())
                if case.expected_rule not in ('SCHEMA', 'IFACE-001'):
                    self.assertIn(case.expected_rule,
                                  {v['requirement'] for r in response for v in r.get('violations', [])})

    def test_no_hidden_truth_in_tool_responses(self):
        i = self.interface('C')
        payload = json.dumps(i.call('get_state', {})) + json.dumps(i.call('acquire', {'scan_index': 0}))
        for secret in SECRETS:
            self.assertNotIn(secret, payload)

    def test_independent_audit_catches_a_deliberately_broken_gate(self):
        i = self.interface('C')
        with patch('obue.simulator.validate_plan', return_value=[]):
            configure(i, {'preamp_enabled': True})
        event = i.analyzer.events[-1]
        self.assertTrue(event['executed'])
        self.assertEqual(event['violations'], [])
        self.assertIn('SAFE-001', audit_plan(event['candidate_plan'], 0))

    def test_independent_audit_accepts_valid_plan(self):
        self.assertEqual(audit_plan(plan_to_dict(default_plan())), [])

    def test_gate_and_audit_agree_for_all_initial_states(self):
        from obue.validation import validate_plan
        for case in scenarios():
            self.assertEqual(bool(audit_plan(plan_to_dict(case.plan))), bool(validate_plan(case.plan)))

    def test_repeated_acquisition_is_reproducible_and_result_not_stale(self):
        i = self.interface('C')
        first = i.call('acquire', {'scan_index': 0})
        second = i.call('acquire', {'scan_index': 0})
        self.assertEqual(first, second)
        configure(i, {'reference_level': {'value': 12, 'unit': 'dBm'}})
        self.assertNotIn(0, i.analyzer.results)

    def test_partial_coverage_cannot_pass_but_valid_failure_is_supported(self):
        self.assertEqual(evidence_decision([], False), 'invalid/incomplete')
        self.assertEqual(evidence_decision([{'value': 0.0, 'offset_hz': 50000}], False), 'fail')
        self.assertEqual(evidence_decision([{'value': -30.0, 'offset_hz': 50000}], True), 'pass')

    def test_summary_keeps_every_session_in_denominator(self):
        record = {'condition': 'C', 'invalid_executions': 0, 'executed_requests': 0, 'invalid_interceptions': 0,
                  'invalid_attempts': 0, 'false_blocks': 0, 'valid_attempts': 0, 'eligible_complete': False,
                  'decision_error': None, 'decision_supported': False, 'adapter_errors': 0}
        self.assertEqual(summarize([record])['by_condition']['C']['eligible_completion']['denominator'], 1)

    def test_decision_error_rate_is_over_reported_pass_fail_sessions(self):
        base = {'condition': 'C', 'invalid_executions': 0, 'executed_requests': 0, 'invalid_interceptions': 0,
                'invalid_attempts': 0, 'false_blocks': 0, 'valid_attempts': 0, 'eligible_complete': False,
                'adapter_errors': 0}
        records = [dict(base, decision_error=True, decision_supported=False),   # wrong, unsupported
                   dict(base, decision_error=False, decision_supported=True),   # right
                   dict(base, decision_error=None, decision_supported=False)]   # unparsed
        rate = summarize(records)['by_condition']['C']['decision_error']
        self.assertEqual((rate['numerator'], rate['denominator'], rate['rate']), (1, 2, 0.5))

    def test_mcp_tool_schemas_are_valid_json_schema(self):
        from jsonschema import Draft202012Validator
        for condition in ('A', 'B', 'C'):
            for tool in self.interface(condition).tools():
                Draft202012Validator.check_schema(tool['inputSchema'])

    def test_mcp_lifecycle_and_tools(self):
        s = Server(self.interface('C'))
        self.assertIn('error', s.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}))
        initialized = s.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize',
                                'params': {'protocolVersion': '2025-03-26'}})
        self.assertEqual(initialized['result']['protocolVersion'], '2025-03-26')
        self.assertEqual(initialized['result']['serverInfo']['name'], 'fsva3000-obue-simulator')
        result = s.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                           'params': {'name': 'acquire', 'arguments': {'scan_index': 0}}})
        self.assertFalse(result['result']['isError'])
        self.assertIn('structuredContent', result['result'])

    def test_real_stdio_transport(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
        for condition, scenario in [('A', None), ('B', None), ('C', None), ('C', 'R03')]:
            messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                         'params': {'protocolVersion': '2025-11-25', 'capabilities': {},
                                    'clientInfo': {'name': 'test', 'version': '1'}}},
                        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
            if condition == 'A':
                calls = [('execute_scpi', {'command': 'FREQ:CENT?'}),
                         ('execute_scpi', {'command': 'BAND:RES 100kHz', 'scan_index': 0}),
                         ('execute_scpi', {'command': 'INIT:CONT OFF'}),
                         ('execute_scpi', {'command': 'INIT:IMM', 'scan_index': 0}),
                         ('execute_scpi', {'command': '*OPC?'}),
                         ('execute_scpi', {'command': 'TRAC:DATA? TRACE1', 'scan_index': 0})]
            else:
                rbw = {'value': 100, 'unit': 'kHz'} if condition == 'C' else 100000
                calls = [('get_state', {}), ('configure_scan', {'scan_index': 0, 'rbw': rbw}),
                         ('acquire', {'scan_index': 0})]
            for ident, (name, arguments) in enumerate(calls, 3):
                messages.append({'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call',
                                 'params': {'name': name, 'arguments': arguments}})
            command = [sys.executable, '-m', 'obue.mcp_server', '--condition', condition]
            if scenario:
                command += ['--scenario', scenario]
            p = subprocess.run(command, input='\n'.join(map(json.dumps, messages)) + '\n',
                               capture_output=True, text=True, env=env, timeout=60)
            with self.subTest(condition=condition, scenario=scenario):
                self.assertEqual(p.returncode, 0, p.stderr)
                responses = [json.loads(line) for line in p.stdout.splitlines()]
                self.assertEqual([r['id'] for r in responses], list(range(1, 3 + len(calls))))
                if condition == 'A':
                    self.assertEqual(float(responses[2]['result']['content'][0]['text']), 3550e6)
                    self.assertEqual(responses[-2]['result']['content'][0]['text'], '1')
                    self.assertEqual(len(responses[-1]['result']['content'][0]['text'].split(',')), 50)
                    self.assertFalse(responses[-1]['result']['isError'])
                    continue
                state = json.loads(responses[2]['result']['content'][0]['text'])
                self.assertEqual(state['instrument_family'], 'R&S FSVA3000')
                acquired = json.loads(responses[-1]['result']['content'][0]['text'])
                self.assertFalse(responses[-1]['result']['isError'])
                self.assertEqual(len(acquired['readings']), 50)
                for secret in SECRETS:
                    self.assertNotIn(secret, p.stdout)

    def test_raw_commands_use_vendor_queries_and_reject_sim_extensions(self):
        i = self.interface('A')
        for command in ('SIM:STATE?', 'SIM:ACQ?', 'SIM:BAND:INT ON', 'SIM:BAND:ENBW 10000'):
            self.assertIn('error', i.call('execute_scpi', {'command': command}))
        for command, expected in [('BAND:RES?', 100000), ('INP:ATT?', 10), ('INP:GAIN:STAT?', 0)]:
            self.assertEqual(float(i.call('execute_scpi', {'command': command})), expected)
        self.assertEqual(i.call('execute_scpi', {'command': 'DET?'}), 'RMS')


if __name__ == '__main__':
    unittest.main()
