"""Scripted integration pilot. NEVER label these sessions as LLM experiments."""
import hashlib
import json
from pathlib import Path
import random
import time

from .config import ROOT
from .audit import audit_plan
from .domain import Unit, Quantity, decision_for_upper_limit, obue_limit_dbm
from .interfaces import Interface, SCPI_FIELDS, SCPI_ENUM_VALUES, UNITS, scpi_enum_token
from .plan import plan_to_dict
from .reference import evaluate_reference
from .scenarios import scenarios, public_task
from .simulator import Analyzer
from .spectrum import make_obue_fixture

ORDER_SEED = 20260919


def response_dict(result):
    """Tool responses as dicts; non-JSON SCPI replies are wrapped."""
    if not isinstance(result, str):
        return result
    try:
        return json.loads(result)
    except ValueError:
        return {'scpi_response': result}


def apply_probe(interface, probe):
    """Send the scenario's proposed settings to scan 0 in the interface's own idiom."""
    if not probe:
        return []
    if interface.condition == 'C':
        return [response_dict(interface.call('configure_scan', {'scan_index': 0, **probe}))]
    if interface.condition == 'B':
        return [response_dict(interface.call('configure_scan', _primitive_probe(probe)))]
    return _apply_scpi_probe(interface, probe)


def _primitive_probe(probe):
    args = {'scan_index': 0}
    for key, value in probe.items():
        if isinstance(value, dict):
            try:
                value = Quantity(value['value'], Unit(value['unit'])).to(UNITS[key]).value
            except ValueError:
                value = value['value']  # Deliberate dimension-loss probe, documented in pilot.
        args[key] = value
    return args


def _apply_scpi_probe(interface, probe):
    # The raw interface expresses compound updates as sequential commands.
    commands = []
    unsupported = []
    for key, value in probe.items():
        header = next((h for h, field in SCPI_FIELDS.items() if field == key), None)
        if header is None:
            unsupported.append({'error': 'No supported R&S SCPI command for benchmark metadata ' + key})
            continue
        if isinstance(value, dict):
            value = f"{value['value']}{value['unit']}"
        elif isinstance(value, bool):
            value = 'ON' if value else 'OFF'
        elif key in SCPI_ENUM_VALUES:
            value = scpi_enum_token(key, value)
        commands.append(f'{header} {value}')
    # SCPI state changes are intentionally sequential; trace metrics preserve this.
    responses = [response_dict(interface.call('execute_scpi', {'command': c, 'scan_index': 0})) for c in commands]
    return responses + unsupported


def run_session(case, condition, rep, *, max_calls=50, timeout_s=180, spectrum=None):
    """Run one scripted session: read state, apply probe, acquire all scans, decide, score."""
    backend = Analyzer(case.plan, make_obue_fixture(case.margin_db) if spectrum is None else spectrum)
    task = public_task(case)
    interface = Interface(backend, condition)
    start = time.perf_counter()
    initial_hash = backend.snapshot()
    status = 'completed'
    readings = []
    valid_readings = []
    probe_responses = []

    def out_of_time():
        return time.perf_counter() - start >= timeout_s

    def call(name, args):
        nonlocal status
        if len(interface.calls) >= max_calls:
            status = 'call_budget'
            return {'error': 'call budget exhausted'}
        if out_of_time():
            status = 'timeout'
            return {'error': 'time budget exhausted'}
        return response_dict(interface.call(name, args))

    # 1. Read the initial state.
    if condition == 'A':
        for header in SCPI_FIELDS:
            call('execute_scpi', {'command': header + '?'})
        call('execute_scpi', {'command': 'INIT:CONT OFF'})
    else:
        call('get_state', {})

    # 2. Apply the proposed (possibly invalid) settings. Probe calls are budgeted too,
    #    including multi-command A updates.
    if case.probe:
        if len(interface.calls) + len(case.probe) > max_calls:
            status = 'call_budget'
        elif out_of_time():
            status = 'timeout'
        else:
            probe_responses = apply_probe(interface, task['proposed_settings'])

    # 3. Acquire every scan; keep readings whose acquisition state passes the audit.
    if status == 'completed':
        for index in range(len(case.plan.scans)):
            if condition == 'A':
                call('execute_scpi', {'command': 'INIT:IMM', 'scan_index': index})
                call('execute_scpi', {'command': '*OPC?'})
                trace = call('execute_scpi', {'command': 'TRAC:DATA? TRACE1', 'scan_index': index})
                # Scripted pilot uses public measurement metadata from the backend;
                # raw SCPI itself returns only trace powers, not a full plan or oracle.
                trace_ok = status == 'completed' and not (isinstance(trace, dict) and 'error' in trace)
                result = backend.results.get(index, {}) if trace_ok else {}
            else:
                result = call('acquire', {'scan_index': index})
            if 'readings' in result:
                readings.extend(result['readings'])
                if not audit_plan(plan_to_dict(backend.plan), index):
                    valid_readings.extend(result['readings'])
            if status != 'completed':
                break

    # 4. Decide from the measurements and compare with the private reference.
    full_errors = audit_plan(plan_to_dict(backend.plan))
    covered = set(backend.results) == set(range(len(case.plan.scans)))
    eligible = covered and not full_errors
    reported = _reported_decision(valid_readings, eligible)
    reference = evaluate_reference(backend.spectrum, case.plan)
    supported = reported == 'fail' or eligible

    # 5. Audit every backend event independently of the condition-C gate.
    for event in backend.events:
        state = event.get('candidate_plan', event.get('acquisition_plan'))
        event['audit_rules'] = audit_plan(state, event['scan_index'])

    return {'scenario': case.id, 'family': case.family, 'condition': condition, 'repetition': rep,
            'actor': 'scripted_integration_probe', 'scope': case.plan.scope,
            'public_task': task,
            'initial_state_hash': initial_hash, 'final_state_hash': backend.snapshot(), 'termination': status,
            'calls': len(interface.calls), 'elapsed_s': time.perf_counter() - start,
            'simulated_time_s': sum(e.get('simulated_time_s', 0) for e in backend.events),
            **_event_counts(backend.events),
            'adapter_errors': len(interface.errors), 'measurement_rows': len(readings), 'eligible_complete': eligible,
            'reported_decision': reported, 'reference_decision': reference['decision'],
            'decision_supported': supported,
            'decision_error': reported != reference['decision'] if supported else None,
            'plan_violations': full_errors,
            'probe_responses': probe_responses,
            'trace': interface.calls, 'backend_events': backend.events}


def _reported_decision(valid_readings, eligible):
    """One valid above-limit reading supports FAIL; PASS needs complete eligible coverage."""
    for reading in valid_readings:
        if decision_for_upper_limit(reading['value'], obue_limit_dbm(reading['offset_hz'])).value == 'fail':
            return 'fail'
    return 'pass' if eligible else 'invalid/incomplete'


def _event_counts(events):
    """Execution and gating counts; an event is invalid when the audit flags any rule."""
    executed = [e for e in events if e['executed']]
    rejected = [e for e in events if not e['executed']]
    return {'executed_requests': len(executed),
            'invalid_executions': sum(bool(e['audit_rules']) for e in executed),
            'invalid_attempts': sum(bool(e['audit_rules']) for e in events),
            'invalid_interceptions': sum(bool(e['audit_rules']) for e in rejected),
            'valid_attempts': sum(not e['audit_rules'] for e in events),
            'false_blocks': sum(not e['audit_rules'] for e in rejected)}


def source_hash():
    """Hash of the package source, rules and schemas that produced a run."""
    h = hashlib.sha256()
    for f in sorted(Path(__file__).parent.glob('*.py')):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    for pattern in ('requirements/*.json', 'schemas/*.json'):
        for f in sorted(ROOT.glob(pattern)):
            h.update(f.relative_to(ROOT).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def _ratio(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def summarize(records):
    result = {'actor': 'scripted_integration_probe',
              'warning': 'Not evidence of LLM performance or causal interface effects.',
              'sessions': len(records), 'by_condition': {}}
    for condition in ('A', 'B', 'C'):
        rows = [r for r in records if r['condition'] == condition]

        def total(key):
            return sum(r[key] for r in rows)

        result['by_condition'][condition] = {
            'sessions': len(rows),
            'invalid_execution': _ratio(total('invalid_executions'), total('executed_requests')),
            'interception': _ratio(total('invalid_interceptions'), total('invalid_attempts')),
            'false_block': _ratio(total('false_blocks'), total('valid_attempts')),
            'eligible_completion': _ratio(total('eligible_complete'), len(rows)),
            'decision_error': _ratio(sum(r['decision_error'] is True for r in rows), total('decision_supported')),
            'adapter_errors': total('adapter_errors')}
    return result


def run_pilot(output, repetitions=1, *, signal='analytical', nr_directory=None):
    """Run every scenario under A/B/C in a seeded shuffled order; write per-session JSON and a summary."""
    if not 1 <= repetitions <= 5:
        raise ValueError('repetitions must be 1..5')
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)

    # NR cases share input directories; load and hash each directory once.
    directories = {}
    spectra = {}
    signal_hashes = {}
    if signal == 'nr':
        from .nr import nr_scenarios, load_nr_spectra, iq_hashes, nr_directory as case_directory
        cases = nr_scenarios()
        base = Path(nr_directory or ROOT / 'outputs/nr_reference')
        for case in cases:
            path = directories[case.id] = case_directory(base, case.id)
            if path not in spectra:
                spectra[path] = load_nr_spectra(path)
                signal_hashes[path] = iq_hashes(path, spectra[path])
    else:
        cases = scenarios()

    schedule = [(s, c, r) for s in cases for c in ('A', 'B', 'C') for r in range(repetitions)]
    random.Random(ORDER_SEED).shuffle(schedule)
    records = []
    revision = source_hash()
    for case, condition, rep in schedule:
        path = directories.get(case.id)
        record = run_session(case, condition, rep, spectrum=spectra.get(path))
        record['source_hash'] = revision
        if signal == 'nr':
            record['signal_hashes'] = signal_hashes[path]
        records.append(record)
        (out / f'{case.id}_{condition}_{rep}.json').write_text(json.dumps(record, indent=2, allow_nan=False))

    summary = summarize(records)
    summary['source_hash'] = revision
    summary['order_seed'] = ORDER_SEED
    summary['order'] = [f'{s.id}/{c}/{r}' for s, c, r in schedule]
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    return summary
