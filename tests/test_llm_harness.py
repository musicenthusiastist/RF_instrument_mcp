import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

try:
    from anthropic.types import TextBlock, ToolUseBlock
except ImportError:  # The harness is optional; skip when the SDK is not installed.
    TextBlock = ToolUseBlock = None

from obue.nr import load_nr_spectra
from obue.scenarios import scenarios


def response(blocks, stop_reason):
    usage = SimpleNamespace(input_tokens=100, output_tokens=10,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage)


def tool_use(i, name, arguments):
    return ToolUseBlock(type='tool_use', id=f'toolu_{i:03}', name=name, input=arguments)


def text(value):
    return TextBlock(type='text', text=value)


class FakeClient:
    """Replays scripted responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **request):
        # Snapshot messages: the harness keeps appending to the same list.
        self.requests.append({**request, 'messages': list(request['messages'])})
        return self.responses.pop(0)


@unittest.skipIf(ToolUseBlock is None, 'anthropic SDK not installed')
class LlmHarnessTests(unittest.TestCase):
    def setUp(self):
        from obue.llm_harness import run_llm_session
        self.case = next(c for c in scenarios() if c.id == 'U01')  # clean NR signal
        self.scans = len(self.case.plan.scans)
        spectra = load_nr_spectra(ROOT / 'outputs/nr_reference')

        def run_session(case, condition, rep, **options):
            return run_llm_session(case, condition, rep, spectrum=spectra, **options)
        self.run_session = run_session

    def test_complete_session_is_scored_against_the_reference(self):
        acquires = [tool_use(i + 1, 'acquire', {'scan_index': i}) for i in range(self.scans)]
        client = FakeClient([
            response([tool_use(0, 'get_state', {})], 'tool_use'),
            response(acquires, 'tool_use'),  # parallel calls in one turn
            response([text('All filters are below their limits.\nDECISION: PASS')], 'end_turn')])
        record = self.run_session(self.case, 'C', 0, client=client)
        self.assertEqual(record['termination'], 'completed')
        self.assertEqual(record['calls'], self.scans + 1)
        self.assertTrue(record['eligible_complete'])
        self.assertEqual((record['reported_decision'], record['evidence_decision'], record['reference_decision']),
                         ('pass', 'pass', 'pass'))
        self.assertTrue(record['decision_supported'])
        self.assertIs(record['decision_error'], False)
        self.assertEqual(record['invalid_executions'], 0)
        # All parallel results go back in a single user message, matched by tool_use_id.
        results = client.requests[2]['messages'][-1]['content']
        self.assertEqual([r['tool_use_id'] for r in results], [b.id for b in acquires])
        self.assertEqual(client.requests[0]['model'], 'claude-opus-5-5')
        self.assertEqual(client.requests[0]['tool_choice'], {'type': 'auto'})
        json.dumps(record, allow_nan=False)  # The saved record must serialize.

    def test_blocked_setting_is_returned_as_tool_error(self):
        client = FakeClient([
            response([tool_use(0, 'configure_scan', {'scan_index': 0, 'detector': 'peak'})], 'tool_use'),
            response([text('DECISION: INVALID/INCOMPLETE')], 'end_turn')])
        record = self.run_session(self.case, 'C', 0, client=client)
        result = client.requests[1]['messages'][-1]['content'][0]
        self.assertTrue(result['is_error'])
        self.assertIn('PROC-002', result['content'])
        self.assertEqual(record['invalid_interceptions'], 1)
        self.assertEqual(record['reported_decision'], 'invalid/incomplete')
        self.assertIsNone(record['decision_error'])

    def test_call_budget_forces_one_answer_turn_without_tools(self):
        calls = [tool_use(i, 'acquire', {'scan_index': i}) for i in range(3)]
        client = FakeClient([response(calls, 'tool_use'),
                             response([text('Ran out of calls.\nDECISION: INVALID/INCOMPLETE')], 'end_turn')])
        record = self.run_session(self.case, 'C', 0, client=client, max_calls=2)
        self.assertEqual(record['termination'], 'call_budget')
        self.assertEqual(record['calls'], 2)
        self.assertEqual(client.requests[1]['tool_choice'], {'type': 'none'})
        self.assertEqual(client.requests[1]['messages'][-1]['content'][2]['content'], 'call budget exhausted')

    def test_unparsed_answer_is_not_scored_as_a_decision(self):
        client = FakeClient([response([text('I think it passes.')], 'end_turn')])
        record = self.run_session(self.case, 'A', 0, client=client)
        self.assertEqual(record['reported_decision'], 'unparsed')
        self.assertIsNone(record['decision_error'])
        self.assertFalse(record['eligible_complete'])


if __name__ == '__main__':
    unittest.main()
