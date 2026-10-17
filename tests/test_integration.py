import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import unittest
import json
import os
import subprocess
from obue.benchmark import run_session,apply_probe,response_dict,summarize
from obue.interfaces import Interface
from obue.plan import default_plan
from obue.scenarios import scenarios,public_task
from obue.simulator import Analyzer
from obue.spectrum import make_obue_fixture
from obue.mcp_server import Server
from obue.audit import audit_plan
from obue.plan import plan_to_dict
from unittest.mock import patch


class IntegrationTests(unittest.TestCase):
    def interface(self,c):return Interface(Analyzer(default_plan(),make_obue_fixture()),c)

    def test_same_public_task_for_all_conditions(self):
        case=next(c for c in scenarios() if c.id=='U01')
        tasks=[run_session(case,condition,0)['public_task'] for condition in ('A','B','C')]
        self.assertEqual(tasks[0],tasks[1]);self.assertEqual(tasks[1],tasks[2])
        self.assertEqual(tasks[0]['plan']['external_loss_db'],40)
        for key in ('seed','uncertainty','calibration'):self.assertNotIn(key,tasks[0])

    def test_public_task_export_matches_controller_input_without_private_labels(self):
        import tempfile
        from obue.scenarios import export_scenarios
        with tempfile.TemporaryDirectory() as directory:
            export_scenarios(directory)
            for case in scenarios():
                exported=json.loads((Path(directory)/'public'/f'{case.id}.json').read_text())
                self.assertEqual(exported,public_task(case))
                for private_key in ('family','description','margin_db','expected_probe_rule','initial_violations','reference','spectrum'):
                    self.assertNotIn(private_key,exported)

    def test_valid_acquisition_same_numeric_results_all_arms(self):
        output=[]
        for c in ('A','B','C'):
            i=self.interface(c)
            if c=='A':
                i.call('execute_scpi',{'command':'INIT:CONT OFF'})
                i.call('execute_scpi',{'command':'INIT:IMM','scan_index':0})
                self.assertEqual(i.call('execute_scpi',{'command':'*OPC?'}),'1')
                trace=i.call('execute_scpi',{'command':'TRAC:DATA? TRACE1','scan_index':0})
                output.append([float(v) for v in trace.split(',')])
            else:output.append([r['value'] for r in i.call('acquire',{'scan_index':0})['readings']])
        self.assertEqual(output[0],output[1]);self.assertEqual(output[1],output[2])

    def test_invalid_state_executed_by_baselines_blocked_atomically_in_c(self):
        for c in ('A','B','C'):
            i=self.interface(c);before=i.analyzer.snapshot()
            apply_probe(i,{'preamp_enabled':True})
            self.assertEqual(i.analyzer.snapshot()==before,c=='C')
            self.assertEqual(i.analyzer.events[-1]['executed'],c!='C')

    def test_typed_unit_equivalence(self):
        for c in ('A','B','C'):
            i=self.interface(c);apply_probe(i,{'rbw':{'value':.1,'unit':'MHz'}})
            self.assertFalse(i.errors);self.assertTrue(i.analyzer.events[-1]['executed'])

    def test_unsupported_arguments_and_malformed_input_no_mutation(self):
        for c in ('B','C'):
            i=self.interface(c);before=i.analyzer.snapshot()
            for args in ({'scan_index':True,'preamp_enabled':True},{'scan_index':0,'unknown':1}, {'scan_index':0,'preamp_enabled':'false'}):
                self.assertIn('error',i.call('configure_scan',args));self.assertEqual(i.analyzer.snapshot(),before)

    def test_full_demo(self):
        r=run_session(scenarios()[0],'C',0)
        self.assertTrue(r['eligible_complete']);self.assertEqual(r['measurement_rows'],1320)
        self.assertFalse(r['decision_error']);self.assertEqual(r['invalid_executions'],0)

    def test_failing_dut_is_valid_and_not_blocked(self):
        case=next(s for s in scenarios() if s.id=='N06')
        result=run_session(case,'C',0)
        self.assertTrue(result['eligible_complete'])
        self.assertEqual(result['reported_decision'],'fail')
        self.assertEqual(result['false_blocks'],0)

    def test_partial_coverage_cannot_pass_but_valid_failure_is_supported(self):
        nominal=run_session(scenarios()[0],'C',0,max_calls=2)
        self.assertEqual(nominal['reported_decision'],'invalid/incomplete')
        failing=run_session(next(s for s in scenarios() if s.id=='N06'),'C',0,max_calls=2)
        self.assertEqual(failing['reported_decision'],'fail')
        self.assertTrue(failing['decision_supported'])

    def test_budget_preserves_session_denominator(self):
        r=run_session(scenarios()[0],'C',0,max_calls=2)
        self.assertEqual(r['termination'],'call_budget');self.assertFalse(r['eligible_complete'])
        self.assertEqual(summarize([r])['by_condition']['C']['eligible_completion']['denominator'],1)

    def test_scenarios_balanced_and_c_intercepts_declared_probes(self):
        cases=scenarios();self.assertEqual(len(cases),18)
        for family in {s.family for s in cases}:self.assertEqual(sum(s.family==family for s in cases),6)
        for case in cases:
            if not case.probe:continue
            i=self.interface('C');response=apply_probe(i,case.probe)
            with self.subTest(case=case.id):
                self.assertTrue(any('error' in r or r.get('blocked') for r in response))
                self.assertEqual(i.analyzer.plan,default_plan())
                if case.expected_rule not in ('SCHEMA','IFACE-001'):
                    self.assertIn(case.expected_rule,{v['requirement'] for r in response for v in r.get('violations',[])})

    def test_no_hidden_truth_in_tool_responses(self):
        i=self.interface('C')
        payload=json.dumps(i.call('get_state',{}))+json.dumps(i.call('acquire',{'scan_index':0}))
        for secret in ('reference_decision','margin_db','expected_probe_rule','reference_power'):
            self.assertNotIn(secret,payload)

    def test_independent_audit_catches_a_deliberately_broken_gate(self):
        i=self.interface('C')
        with patch('obue.simulator.validate_plan',return_value=[]):
            apply_probe(i,{'preamp_enabled':True})
        event=i.analyzer.events[-1]
        self.assertTrue(event['executed']);self.assertEqual(event['violations'],[])
        self.assertIn('SAFE-001',audit_plan(event['candidate_plan'],0))

    def test_independent_audit_accepts_valid_plan(self):
        self.assertEqual(audit_plan(plan_to_dict(default_plan())),[])

    def test_gate_and_audit_agree_for_all_provisional_initial_states(self):
        from obue.validation import validate_plan
        for case in scenarios():
            self.assertEqual(bool(audit_plan(plan_to_dict(case.plan))),bool(validate_plan(case.plan)))

    def test_repeated_acquisition_is_reproducible_and_result_not_stale(self):
        i=self.interface('C');first=i.call('acquire',{'scan_index':0});second=i.call('acquire',{'scan_index':0})
        self.assertEqual(first,second)
        apply_probe(i,{'reference_level':{'value':12,'unit':'dBm'}})
        self.assertNotIn(0,i.analyzer.results)

    def test_mcp_tool_schemas_validate_nominal_arguments(self):
        from jsonschema import Draft202012Validator
        for condition in ('A','B','C'):
            for tool in self.interface(condition).tools():Draft202012Validator.check_schema(tool['inputSchema'])

    def test_mcp_lifecycle_and_tools(self):
        s=Server(self.interface('C'))
        self.assertIn('error',s.handle({'jsonrpc':'2.0','id':1,'method':'tools/list'}))
        initialized=s.handle({'jsonrpc':'2.0','id':2,'method':'initialize','params':{'protocolVersion':'2025-03-26'}})
        self.assertEqual(initialized['result']['protocolVersion'],'2025-03-26')
        self.assertEqual(initialized['result']['serverInfo']['name'],'fsva3000-obue-simulator')
        result=s.handle({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'acquire','arguments':{'scan_index':0}}})
        self.assertFalse(result['result']['isError']);self.assertIn('structuredContent',result['result'])

    def test_real_stdio_transport(self):
        env=dict(os.environ,PYTHONPATH=str(ROOT/'src'))
        for condition,scenario in [('A','N01'),('B','N01'),('C','N01'),('C','N06')]:
            messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
                      {'jsonrpc':'2.0','method':'notifications/initialized'},
                      {'jsonrpc':'2.0','id':2,'method':'tools/list'}]
            calls=([('execute_scpi',{'command':'FREQ:CENT?'}),
                    ('execute_scpi',{'command':'BAND:RES 100kHz','scan_index':0}),
                    ('execute_scpi',{'command':'INIT:CONT OFF'}),
                    ('execute_scpi',{'command':'INIT:IMM','scan_index':0}),
                    ('execute_scpi',{'command':'*OPC?'}),
                    ('execute_scpi',{'command':'TRAC:DATA? TRACE1','scan_index':0})] if condition=='A' else
                   [('get_state',{}),('configure_scan',{'scan_index':0,'rbw':{'value':100,'unit':'kHz'} if condition=='C' else 100000}),
                    ('acquire',{'scan_index':0})])
            for ident,(name,arguments) in enumerate(calls,3):
                messages.append({'jsonrpc':'2.0','id':ident,'method':'tools/call','params':{'name':name,'arguments':arguments}})
            p=subprocess.run([sys.executable,'-m','obue.mcp_server','--condition',condition,'--scenario',scenario],input='\n'.join(map(json.dumps,messages))+'\n',capture_output=True,text=True,env=env,timeout=20)
            with self.subTest(condition=condition,scenario=scenario):
                self.assertEqual(p.returncode,0,p.stderr)
                responses=[json.loads(line) for line in p.stdout.splitlines()]
                self.assertEqual([r['id'] for r in responses],list(range(1,3+len(calls))))
                if condition=='A':
                    self.assertEqual(float(responses[2]['result']['content'][0]['text']),3550e6)
                    self.assertEqual(responses[-2]['result']['content'][0]['text'],'1')
                    self.assertEqual(len(responses[-1]['result']['content'][0]['text'].split(',')),50)
                    self.assertFalse(responses[-1]['result']['isError'])
                    continue
                state=json.loads(responses[2]['result']['content'][0]['text'])
                self.assertEqual(state['instrument_family'],'R&S FSVA3000')
                acquired=json.loads(responses[-1]['result']['content'][0]['text'])
                self.assertEqual(responses[-1]['result']['isError'],False)
                self.assertEqual(len(acquired['readings']),50)
                for secret in ('reference_decision','margin_db','expected_probe_rule','reference_power'):
                    self.assertNotIn(secret,p.stdout)

    def test_raw_commands_use_vendor_queries_and_reject_sim_extensions(self):
        i=self.interface('A')
        for command in ('SIM:STATE?','SIM:ACQ?','SIM:BAND:INT ON','SIM:BAND:ENBW 10000'):
            self.assertIn('error',i.call('execute_scpi',{'command':command}))
        for command,expected in [('BAND:RES?',100000),('INP:ATT?',10),('INP:GAIN:STAT?',0)]:
            self.assertEqual(float(i.call('execute_scpi',{'command':command})),expected)
        self.assertEqual(i.call('execute_scpi',{'command':'DET?'}),'RMS')


if __name__=='__main__':unittest.main()
