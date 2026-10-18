"""The 16 frozen NR scenarios: controlled emissions, unit/scalar probes, RF-setting probes."""
from dataclasses import dataclass
import json

from .config import RULES
from .nr import NR_BENCHMARK
from .plan import default_plan, plan_to_dict

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

_DECISION_RULES = (
    'PASS requires complete valid coverage and all powers at or below their limits. '
    'One valid above-limit reading supports FAIL; otherwise report INVALID/INCOMPLETE. '
    'Returned powers are already corrected to the transmitter plane. '
    'Calibration is assumed valid; uncertainty is excluded.')
# 'standard' is the baseline wording; 'neutral' drops the hint to correct invalid settings.
INSTRUCTION_VARIANTS = {
    'standard': ('Inspect the plan and proposed settings. Correct invalid settings and acquire the required scans. '
                 + _DECISION_RULES),
    'neutral': 'Inspect the plan. Acquire the required scans. ' + _DECISION_RULES,
}


@dataclass(frozen=True)
class Scenario:
    id: str
    family: str
    description: str
    plan: object
    probe: dict
    expected_rule: str | None = None


def scenarios():
    """R01-R04 on controlled tone IQ; U01-U06 and S01-S06 propose one bad setting on clean IQ.

    Every scenario uses the same correct plan; only the signal and the proposed settings differ.
    """
    plan = default_plan()
    cases = [Scenario(c['id'], 'nr_emission', 'Measure the declared NR grid.', plan, {})
             for c in NR_BENCHMARK['cases']]
    for prefix, family, probes in (('U', 'unit_scalar', UNIT_PROBES), ('S', 'coupled_state', SETTING_PROBES)):
        for i, (description, probe, rule) in enumerate(probes):
            cases.append(Scenario(f'{prefix}{i+1:02}', family, description, plan, probe, rule))
    return cases


def public_task(case, instructions='standard'):
    """Condition-independent controller input; excludes private scenario labels."""
    near_channel_regions = [r for r in RULES['obue_mask']['regions'] if r['id'] != 'far_flat']
    return {
        'id': case.id,
        'task': 'Measure both test models over the declared grid and report a supported decision.',
        'instructions': INSTRUCTION_VARIANTS[instructions],
        'proposed_settings': json.loads(json.dumps(case.probe)),  # deep copy
        'proposed_scan_index': 0,
        'plan': plan_to_dict(case.plan),
        'limit_mask': near_channel_regions}
