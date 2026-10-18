"""Session scoring shared by every controller: audit, evidence rule, and summary metrics."""
import hashlib
from pathlib import Path

from .audit import audit_plan
from .config import ROOT
from .domain import decision_for_upper_limit, obue_limit_dbm

ORDER_SEED = 20260919


def audit_events(events):
    """Attach independent audit rule IDs to every configure/acquire event."""
    for event in events:
        state = event.get('candidate_plan', event.get('acquisition_plan'))
        event['audit_rules'] = audit_plan(state, event['scan_index'])


def valid_readings(backend):
    """Readings from each scan's latest acquisition, kept only if its acquisition state passed the audit.

    Call audit_events first.
    """
    readings = []
    for index, result in sorted(backend.results.items()):
        latest = [e for e in backend.events
                  if e['operation'] == 'acquire' and e['scan_index'] == index and 'rows' in e][-1]
        if not latest['audit_rules']:
            readings.extend(result['readings'])
    return readings


def evidence_decision(readings, eligible):
    """One valid above-limit reading supports FAIL; PASS needs complete eligible coverage."""
    for reading in readings:
        if decision_for_upper_limit(reading['value'], obue_limit_dbm(reading['offset_hz'])).value == 'fail':
            return 'fail'
    return 'pass' if eligible else 'invalid/incomplete'


def event_counts(events):
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
    """Per-condition rates over session records."""
    result = {'sessions': len(records), 'by_condition': {}}
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
            # Denominator: sessions that reported pass/fail (decision_error is None otherwise).
            'decision_error': _ratio(sum(r['decision_error'] is True for r in rows),
                                     sum(r['decision_error'] is not None for r in rows)),
            'adapter_errors': total('adapter_errors')}
    return result
