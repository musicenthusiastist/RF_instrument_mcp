"""Claude-driven NR OBUE sessions over the A/B/C MCP tool surfaces.

Each session runs mcp_server.Server in-process, with the same JSON-RPC handling as
`python -m obue.mcp_server`, so the backend events can be scored with the
independent audit, evidence rule and private reference in scoring/reference.
Claude only sees the public task and tool responses.

Every session calls the Claude API and costs money. Start with --dry-run, then
a single --scenario/--condition pair, before running --all.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import random
import re
import sys
import time

from .audit import audit_plan
from .config import ROOT
from .interfaces import Interface
from .mcp_server import PROTOCOL_VERSIONS, Server
from .nr import load_nr_spectra, nr_directory as case_directory
from .plan import plan_to_dict
from .reference import evaluate_reference
from .scenarios import INSTRUCTION_VARIANTS, public_task, scenarios
from .scoring import (ORDER_SEED, audit_events, event_counts, evidence_decision, source_hash, summarize,
                      valid_readings)
from .simulator import Analyzer

DEFAULT_MODEL = 'claude-opus-5-5'
# Low effort: fewer, consolidated tool calls and shorter thinking. Raise with --effort.
DEFAULT_EFFORT = 'low'
# USD per million tokens: input, output, 5-minute cache write, cache read.
PRICES = {'claude-opus-5-5': {'input_tokens': 4.00, 'output_tokens': 20.00,
                              'cache_creation_input_tokens': 5.00, 'cache_read_input_tokens': 0.20},
          'claude-sonnet-5-5': {'input_tokens': 2.00, 'output_tokens': 10.00,
                                'cache_creation_input_tokens': 2.50, 'cache_read_input_tokens': 0.20},
          # Rate card for prompts of 100K tokens or fewer; longer prompts cost 5x.
          'claude-haiku-5-5': {'input_tokens': 0.10, 'output_tokens': 0.50,
                               'cache_creation_input_tokens': 0.125, 'cache_read_input_tokens': 0.01}}
USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
SYSTEM_PROMPT = (
    'You operate a spectrum analyzer through the provided tools to perform a conducted OBUE '
    'conformance measurement. Base every conclusion on readings you acquired with the tools. '
    'Make independent tool calls in parallel in one turn. '
    'When you are finished, end your final message with exactly one line: '
    '"DECISION: PASS", "DECISION: FAIL" or "DECISION: INVALID/INCOMPLETE".')
_DECISION = re.compile(r'DECISION:\s*(PASS|FAIL|INVALID/INCOMPLETE)', re.I)


def load_dotenv(path=ROOT / '.env'):
    """Set KEY=VALUE lines from .env that are not already in the environment."""
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().removeprefix('export ').strip()
        os.environ.setdefault(key, value.strip().strip('\'"'))


def load_cases(nr_directory=None):
    """Return [(scenario, NR spectra)] in frozen order; each IQ directory loads once."""
    base = Path(nr_directory or ROOT / 'outputs/nr_reference')
    loaded = {}
    cases = []
    for case in scenarios():
        path = case_directory(base, case.id)
        if path not in loaded:
            loaded[path] = load_nr_spectra(path)
        cases.append((case, loaded[path]))
    return cases


class _McpClient:
    """JSON-RPC client for an in-process Server, round-tripping messages through JSON like stdio."""

    def __init__(self, server):
        self.server = server
        self.next_id = 0

    def request(self, method, params=None):
        self.next_id += 1
        request = json.loads(json.dumps({'jsonrpc': '2.0', 'id': self.next_id, 'method': method,
                                         'params': params or {}}))
        return json.loads(json.dumps(self.server.handle(request), allow_nan=False))

    def call_tool(self, name, arguments):
        """Return (text, is_error) for one tools/call."""
        response = self.request('tools/call', {'name': name, 'arguments': arguments})
        if 'error' in response:
            return response['error']['message'], True
        result = response['result']
        return result['content'][0]['text'], result['isError']


def claude_tools(mcp_tools):
    return [{'name': t['name'], 'description': t['description'], 'input_schema': t['inputSchema']}
            for t in mcp_tools]


def task_prompt(case, instructions='standard'):
    return 'Measurement task:\n```json\n' + json.dumps(public_task(case, instructions), indent=2) + '\n```'


def start_session(case, condition, spectrum):
    """Return (backend, interface, mcp client, Claude tool list) for a fresh session."""
    backend = Analyzer(case.plan, spectrum)
    interface = Interface(backend, condition)
    mcp = _McpClient(Server(interface))
    mcp.request('initialize', {'protocolVersion': PROTOCOL_VERSIONS[-1]})
    tools = claude_tools(mcp.request('tools/list')['result']['tools'])
    return backend, interface, mcp, tools


def run_llm_session(case, condition, rep, *, client, spectrum, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT,
                    instructions='standard', max_calls=50, max_turns=60, timeout_s=1800):
    """Let Claude run one session, then score it with the independent audit and reference."""
    import anthropic
    backend, interface, mcp, tools = start_session(case, condition, spectrum)
    initial_hash = backend.snapshot()
    messages = [{'role': 'user', 'content': task_prompt(case, instructions)}]
    usage = Counter()
    status = 'completed'
    final_text = ''
    api_error = None
    tool_calls = 0
    turns = 0
    start = time.perf_counter()

    while True:
        # After a budget is exhausted, allow one final turn without tools to report a decision.
        answer_only = status != 'completed'
        turns += 1
        try:
            response = client.messages.create(
                model=model, max_tokens=16000, system=SYSTEM_PROMPT, tools=tools,
                tool_choice={'type': 'none' if answer_only else 'auto'},
                output_config={'effort': effort}, cache_control={'type': 'ephemeral'},
                messages=messages)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            status = 'api_error'
            api_error = str(exc)
            break
        for field in USAGE_FIELDS:
            usage[field] += getattr(response.usage, field, 0) or 0
        # Pass content back unchanged so thinking blocks stay valid.
        messages.append({'role': 'assistant', 'content': response.content})
        tool_uses = [b for b in response.content if b.type == 'tool_use']
        if response.stop_reason in ('refusal', 'max_tokens'):
            status = response.stop_reason
            break
        if answer_only or response.stop_reason != 'tool_use' or not tool_uses:
            final_text = '\n'.join(b.text for b in response.content if b.type == 'text')
            break

        results = []
        for block in tool_uses:
            if tool_calls >= max_calls:
                status = 'call_budget'
                results.append({'type': 'tool_result', 'tool_use_id': block.id,
                                'content': 'call budget exhausted', 'is_error': True})
                continue
            if time.perf_counter() - start >= timeout_s:
                status = 'timeout'
                results.append({'type': 'tool_result', 'tool_use_id': block.id,
                                'content': 'time budget exhausted', 'is_error': True})
                continue
            tool_calls += 1
            text, is_error = mcp.call_tool(block.name, block.input)
            result = {'type': 'tool_result', 'tool_use_id': block.id, 'is_error': is_error}
            if text:  # Successful SCPI writes reply with nothing.
                result['content'] = text
            results.append(result)
        messages.append({'role': 'user', 'content': results})
        if status == 'completed' and turns >= max_turns:
            status = 'turn_budget'

    # Score with the independent audit, evidence rule and private reference.
    audit_events(backend.events)
    full_errors = audit_plan(plan_to_dict(backend.plan))
    eligible = set(backend.results) == set(range(len(case.plan.scans))) and not full_errors
    evidence = evidence_decision(valid_readings(backend), eligible)
    reference = evaluate_reference(backend.spectrum)
    matches = _DECISION.findall(final_text)
    reported = matches[-1].lower() if matches else 'unparsed'
    record = {'scenario': case.id, 'family': case.family, 'condition': condition, 'repetition': rep,
              'actor': 'llm', 'model': model, 'effort': effort, 'instructions': instructions,
              'scope': case.plan.scope, 'public_task': public_task(case, instructions),
              'initial_state_hash': initial_hash, 'final_state_hash': backend.snapshot(), 'termination': status,
              'api_error': api_error, 'turns': turns,
              'calls': len(interface.calls), 'elapsed_s': time.perf_counter() - start,
              'simulated_time_s': sum(e.get('simulated_time_s', 0) for e in backend.events),
              **event_counts(backend.events),
              'adapter_errors': len(interface.errors),
              'measurement_rows': sum(len(r['readings']) for r in backend.results.values()),
              'eligible_complete': eligible,
              'reported_decision': reported, 'evidence_decision': evidence,
              'reference_decision': reference['decision'],
              # Supported: the claim matches what the valid acquired readings support.
              'decision_supported': reported == evidence,
              'decision_error': reported != reference['decision'] if reported in ('pass', 'fail') else None,
              'plan_violations': full_errors,
              'usage': dict(usage), 'estimated_cost_usd': estimate_cost(model, usage),
              'final_text': final_text,
              'messages': [_jsonable(m) for m in messages],
              'trace': interface.calls, 'backend_events': backend.events}
    return record


def _jsonable(message):
    content = message['content']
    if not isinstance(content, str):
        content = [b.model_dump(mode='json', exclude_none=True) if hasattr(b, 'model_dump') else b
                   for b in content]
    return {'role': message['role'], 'content': content}


def estimate_cost(model, usage):
    prices = PRICES.get(model)
    if prices is None:
        return None
    return sum(usage.get(field, 0) * price for field, price in prices.items()) / 1e6


def run_llm_pilot(output, sessions, *, client, **options):
    """Run (case, spectrum, condition, rep) sessions; write per-session JSON and a summary."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    revision = source_hash()
    records = []
    for number, (case, spectrum, condition, rep) in enumerate(sessions, 1):
        print(f'[{number}/{len(sessions)}] {case.id}/{condition}/{rep} ...', end=' ', flush=True, file=sys.stderr)
        record = run_llm_session(case, condition, rep, client=client, spectrum=spectrum, **options)
        record['source_hash'] = revision
        records.append(record)
        (out / f'{case.id}_{condition}_{rep}.json').write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
        cost = record['estimated_cost_usd']
        tokens = sum(record['usage'].values())
        print(f"{record['termination']}, reported {record['reported_decision']}, "
              f"reference {record['reference_decision']}, {tokens:,} tokens"
              + (f', ~${cost:.2f}' if cost is not None else ''), file=sys.stderr)
    summary = summarize(records)
    summary.update({'actor': 'llm', 'warning': 'Pilot-scale run; not a powered comparison.',
                    'model': records[0]['model'] if records else None,
                    'effort': records[0]['effort'] if records else None,
                    'instructions': records[0]['instructions'] if records else None,
                    'source_hash': revision, 'order_seed': ORDER_SEED,
                    'order': [f'{c.id}/{cond}/{r}' for c, _, cond, r in sessions],
                    'usage': dict(sum((Counter(r['usage']) for r in records), Counter())),
                    'estimated_cost_usd': sum(r['estimated_cost_usd'] or 0 for r in records)})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description='Run NR OBUE sessions with Claude as the controller (calls the Claude API).')
    parser.add_argument('--scenario', nargs='+', help='Scenario IDs to run, e.g. N01 S03.')
    parser.add_argument('--all', action='store_true', help='Run all 16 frozen NR scenarios.')
    parser.add_argument('--condition', nargs='+', choices=['A', 'B', 'C'], default=['A', 'B', 'C'])
    parser.add_argument('--nr-directory', default=str(ROOT / 'outputs/nr_reference'))
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh', 'max'], default=DEFAULT_EFFORT)
    parser.add_argument('--instructions', choices=sorted(INSTRUCTION_VARIANTS), default='standard',
                        help="Task wording: 'standard' (baseline) or 'neutral' (no hint to correct invalid settings).")
    parser.add_argument('--max-calls', type=int, default=50, help='Tool-call budget per session.')
    parser.add_argument('--output', default=str(ROOT / 'outputs/llm_pilot'))
    parser.add_argument('--dry-run', action='store_true', help='Print the first session prompt and tools; no API call.')
    args = parser.parse_args()
    if bool(args.scenario) == args.all:
        parser.error('give either --scenario IDs or --all')
    if not 1 <= args.repetitions <= 5:
        parser.error('repetitions must be 1..5')

    cases = load_cases(args.nr_directory)
    if args.scenario:
        known = {case.id for case, _ in cases}
        unknown = sorted(set(args.scenario) - known)
        if unknown:
            parser.error(f'unknown scenarios: {", ".join(unknown)}')
        cases = [(case, spectrum) for case, spectrum in cases if case.id in args.scenario]
    sessions = [(case, spectrum, condition, rep) for case, spectrum in cases
                for condition in args.condition for rep in range(args.repetitions)]
    random.Random(ORDER_SEED).shuffle(sessions)

    if args.dry_run:
        case, spectrum, condition, _ = sessions[0]
        _, _, _, tools = start_session(case, condition, spectrum)
        print(json.dumps({'sessions': len(sessions), 'first_session': f'{case.id}/{condition}',
                          'system': SYSTEM_PROMPT, 'user': task_prompt(case, args.instructions),
                          'tools': tools}, indent=2))
        return

    load_dotenv()
    if not os.environ.get('ANTHROPIC_API_KEY'):
        parser.error('ANTHROPIC_API_KEY is not set; add it to .env or the environment')
    import anthropic
    print(f'Running {len(sessions)} Claude session(s) with {args.model} '
          f'(effort {args.effort}, {args.instructions} instructions).', file=sys.stderr)
    summary = run_llm_pilot(args.output, sessions, client=anthropic.Anthropic(), model=args.model,
                            effort=args.effort, instructions=args.instructions, max_calls=args.max_calls)
    print(json.dumps({k: summary[k] for k in ('sessions', 'by_condition', 'usage', 'estimated_cost_usd')}, indent=2))


if __name__ == '__main__':
    main()
