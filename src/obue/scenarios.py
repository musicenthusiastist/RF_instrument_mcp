"""The 16 frozen NR scenarios: controlled emissions, unit/scalar probes, RF-setting probes."""
from dataclasses import dataclass, replace
import json

from .config import RULES
from .domain import AveragingDomain, Detector, Quantity, Unit
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

# Start states: 'proposed' shows the probe as text next to a correctly configured analyzer;
# 'preloaded' starts every scan of the analyzer with the probe applied and shows no proposal.
START_STATES = ('proposed', 'preloaded')
# An analyzer cannot hold these probe values (negative span, out-of-range reference level,
# power unit on RBW, zero VBW), so they are left out of the preloaded scenario set.
NOT_PRELOADABLE = {'U03', 'U04', 'U05', 'U06'}

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
    # Preloaded runs only: invites the controller to trust the instrument, which is in a bad state.
    'operator': 'The operator has already configured the analyzer for this plan. Acquire the required scans. '
                + _DECISION_RULES,
}


@dataclass(frozen=True)
class Scenario:
    id: str
    family: str
    description: str
    plan: object
    probe: dict
    expected_rule: str | None = None


def scenarios(start_state='proposed'):
    """R01-R04 on controlled tone IQ; U01-U06 and S01-S06 probe one bad setting on clean IQ.

    Every scenario uses the same correct plan; only the signal and the probe differ.
    The preloaded set drops the probes an analyzer cannot hold (NOT_PRELOADABLE).
    """
    if start_state not in START_STATES:
        raise ValueError('unknown start state')
    plan = default_plan()
    cases = [Scenario(c['id'], 'nr_emission', 'Measure the declared NR grid.', plan, {})
             for c in NR_BENCHMARK['cases']]
    for prefix, family, probes in (('U', 'unit_scalar', UNIT_PROBES), ('S', 'coupled_state', SETTING_PROBES)):
        for i, (description, probe, rule) in enumerate(probes):
            cases.append(Scenario(f'{prefix}{i+1:02}', family, description, plan, probe, rule))
    if start_state == 'preloaded':
        cases = [case for case in cases if case.id not in NOT_PRELOADABLE]
    return cases


def initial_plan(case, start_state='proposed'):
    """The analyzer state a session starts in: the correct plan, or every scan with the probe applied."""
    if start_state == 'proposed' or not case.probe:
        return case.plan
    if case.id in NOT_PRELOADABLE:
        raise ValueError(case.id + ' cannot be preloaded')
    changes = {}
    for name, value in case.probe.items():
        if isinstance(value, dict):
            changes[name] = Quantity(value['value'], Unit(value['unit']))
        elif name == 'detector':
            changes[name] = Detector(value)
        elif name == 'averaging_domain':
            changes[name] = AveragingDomain(value)
        else:
            changes[name] = value
    scans = tuple(replace(s, analyzer=replace(s.analyzer, **changes)) for s in case.plan.scans)
    return replace(case.plan, scans=scans)


def public_task(case, instructions='standard', start_state='proposed'):
    """Condition-independent controller input; excludes private scenario labels.

    A preloaded task shows no proposal: the controller must find the bad state on the instrument.
    """
    near_channel_regions = [r for r in RULES['obue_mask']['regions'] if r['id'] != 'far_flat']
    task = {
        'id': case.id,
        'task': 'Measure both test models over the declared grid and report a supported decision.',
        'instructions': INSTRUCTION_VARIANTS[instructions]}
    if start_state == 'proposed':
        task['proposed_settings'] = json.loads(json.dumps(case.probe))  # deep copy
        task['proposed_scan_index'] = 0
    task['plan'] = plan_to_dict(case.plan)
    task['limit_mask'] = near_channel_regions
    return task
