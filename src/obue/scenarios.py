"""Deterministic nominal, unit, and RF-setting scenarios."""
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .config import RULES
from .plan import default_plan, plan_to_dict
from .spectrum import make_obue_fixture

NOMINAL_MARGINS_DB = [3, 1, .2, -.2, -1, -3]

# (description, proposed settings for scan 0, rule the probe is meant to trigger)
UNIT_PROBES = [
    ('RBW below required measurement bandwidth', {'rbw': {'value': 100, 'unit': 'Hz'}}, 'PROC-001'),
    ('RBW exceeds near-edge bandwidth', {'rbw': {'value': 3, 'unit': 'MHz'}}, 'PROC-001'),
    ('Negative span', {'span': {'value': -1, 'unit': 'Hz'}}, 'SCHEMA'),
    ('Reference level outside scalar range', {'reference_level': {'value': 100, 'unit': 'dBm'}}, 'SCHEMA'),
    ('Power unit supplied to RBW', {'rbw': {'value': 100, 'unit': 'dBm'}}, 'IFACE-001'),
    ('Zero video bandwidth', {'vbw': {'value': 0, 'unit': 'Hz'}}, 'SCHEMA'),
]
SETTING_PROBES = [
    ('Peak detector', {'detector': 'peak'}, 'PROC-002'),
    ('Logarithmic averaging', {'averaging_domain': 'log'}, 'PROC-002'),
    ('Matched RBW/VBW below required measurement bandwidth',
     {'rbw': {'value': 10, 'unit': 'kHz'}, 'vbw': {'value': 10, 'unit': 'kHz'}}, 'PROC-001'),
    ('Preamp overload', {'preamp_enabled': True}, 'SAFE-001'),
    ('Insufficient attenuation', {'attenuation': {'value': 0, 'unit': 'dB'}}, 'SAFE-001'),
    ('Span misses measurement regions', {'span': {'value': 100, 'unit': 'MHz'}}, 'PROC-004'),
]

INSTRUCTIONS = (
    'Inspect the plan and proposed settings. Correct invalid settings and acquire the required scans. '
    'PASS requires complete valid coverage and all powers at or below their limits. '
    'One valid above-limit reading supports FAIL; otherwise report INVALID/INCOMPLETE. '
    'Returned powers are already corrected to the transmitter plane. '
    'Calibration is assumed valid; uncertainty is excluded.')


@dataclass(frozen=True)
class Scenario:
    id: str
    family: str
    description: str
    margin_db: float
    plan: object
    probe: dict
    expected_rule: str | None = None


def scenarios():
    """N: nominal margins; U: unit/scalar probes; S: coupled RF-setting probes (all at +3 dB)."""
    plan = default_plan()
    cases = [Scenario(f'N{i+1:02}', 'nominal', 'Complete a valid two-model OBUE test.', margin, plan, {})
             for i, margin in enumerate(NOMINAL_MARGINS_DB)]
    for prefix, family, probes in (('U', 'unit_scalar', UNIT_PROBES), ('S', 'coupled_state', SETTING_PROBES)):
        for i, (description, probe, rule) in enumerate(probes):
            cases.append(Scenario(f'{prefix}{i+1:02}', family, description, 3, plan, probe, rule))
    return cases


def public_task(case):
    """Condition-independent controller input; excludes private scenario labels."""
    mask_regions = 2 if case.plan.scope == 'near_channel_nr' else 3
    return {
        'id': case.id,
        'task': 'Measure both test models over the declared grid and report a supported decision.',
        'instructions': INSTRUCTIONS,
        'proposed_settings': json.loads(json.dumps(case.probe)),  # deep copy
        'proposed_scan_index': 0,
        'plan': plan_to_dict(case.plan),
        'limit_mask': RULES['obue_mask']['regions'][:mask_regions]}


def export_scenarios(directory):
    # Private reference calculations are needed only by the offline export.
    from .reference import evaluate_reference
    from .audit import audit_plan
    out = Path(directory)
    (out / 'public').mkdir(parents=True, exist_ok=True)
    (out / 'private').mkdir(exist_ok=True)
    cases = scenarios()
    for c in cases:
        (out / 'public' / f'{c.id}.json').write_text(json.dumps(public_task(c), indent=2))
        spectrum = make_obue_fixture(c.margin_db)
        private = {'id': c.id, 'family': c.family, 'description': c.description,
                   'fixture_type': 'manufactured_piecewise_psd_not_nr_waveform', 'margin_db': c.margin_db,
                   'expected_probe_rule': c.expected_rule, 'initial_violations': audit_plan(plan_to_dict(c.plan)),
                   'reference': evaluate_reference(spectrum, c.plan), 'spectrum': asdict(spectrum)}
        (out / 'private' / f'{c.id}.json').write_text(json.dumps(private, indent=2))
    return len(cases)
