"""Eligibility checks return traceable violations; they never compute truth labels."""
from collections import Counter
from dataclasses import dataclass
from math import isfinite

from .config import CAPABILITY, RULES
from .domain import AveragingDomain, Detector, TestModel, Unit, filter_center_hz
from .plan import Plan

SCOPE = 'near_channel_nr'
SIDES = ('lower', 'upper')
MAX_FILTERS_PER_SCAN = 2000
REGION_FIELDS = ('start_offset', 'stop_offset', 'step', 'measurement_bandwidth')
ANALYZER_FIELDS = (('center_frequency', Unit.HZ), ('span', Unit.HZ), ('rbw', Unit.HZ), ('vbw', Unit.HZ),
                   ('attenuation', Unit.DB), ('reference_level', Unit.DBM))
POSITIVE_ANALYZER_FIELDS = ('span', 'rbw', 'vbw')


@dataclass(frozen=True)
class Violation:
    requirement: str
    location: str
    message: str


class _Checker:
    """Collects violations; number/quantity return None when a value is unusable."""

    def __init__(self):
        self.errors = []

    def fail(self, rule, loc, msg):
        self.errors.append(Violation(rule, loc, msg))

    def number(self, value, rule, loc, *, minimum=None, maximum=None):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            self.fail(rule, loc, 'must be a finite number')
            return None
        if minimum is not None and value < minimum or maximum is not None and value > maximum:
            self.fail(rule, loc, f'outside [{minimum}, {maximum}]')
            return None
        return value

    def quantity(self, q, unit, loc, positive=False):
        try:
            value = q.to(unit).value
        except (ValueError, KeyError, AttributeError):
            self.fail('IFACE-001', loc, f'expected {unit.value} dimension')
            return None
        return self.number(value, 'SCOPE-002', loc, minimum=1e-12 if positive else None)


def validate_plan(plan: Plan, *, scan_index: int | None = None) -> list[Violation]:
    """When scan_index is set, check acquisition eligibility, not whole-plan coverage."""
    check = _Checker()
    _check_profile(plan, check)
    actual_power = check.number(plan.actual_output_power_dbm, 'PROC-003', 'actual_output_power_dbm')
    if plan.actual_output_power_dbm != 46:
        check.fail('PROC-003', 'actual_output_power_dbm', 'DUT is not at declared rated power')
    loss = check.number(plan.external_loss_db, 'SAFE-001', 'external_loss_db', minimum=40, maximum=40)
    if plan.scope != SCOPE:
        check.fail('SCOPE-003', 'scope', 'unknown measurement scope')

    expected = _expected_filters()
    actual = Counter()
    power_known = loss is not None and actual_power is not None
    indices = range(len(plan.scans)) if scan_index is None else [scan_index]
    for i in indices:
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(plan.scans):
            check.fail('PROC-004', 'scan_index', 'index outside plan')
            continue
        _check_scan(plan, i, check, expected, actual, power_known)

    if scan_index is None:
        if actual != expected:
            check.fail('PROC-004', 'scans', 'coverage must contain each expected filter exactly once')
        if {s.test_model for s in plan.scans} != set(TestModel):
            check.fail('PROC-005', 'scans', 'both TM1.1 and TM1.2 are required')
    return list(dict.fromkeys(check.errors))


def _check_profile(plan, check):
    """The DUT and profile must match the frozen n78 research profile."""
    if plan.profile_id != RULES['metadata']['profile_id']:
        check.fail('SCOPE-003', 'profile_id', 'unknown frozen profile')
    for field, expected in [('band', 'n78'), ('bs_type', '1-C'), ('bs_class', 'wide_area'), ('emission_category', 'A')]:
        if getattr(plan.dut, field) != expected:
            check.fail('SCOPE-001' if field == 'band' else 'SCOPE-003', 'dut.' + field, 'outside frozen profile')
    for field, unit, expected in [('carrier_center', Unit.HZ, 3550e6), ('channel_bandwidth', Unit.HZ, 100e6),
                                  ('subcarrier_spacing', Unit.HZ, 30e3), ('rated_output_power', Unit.DBM, 46)]:
        value = check.quantity(getattr(plan.dut, field), unit, 'dut.' + field)
        if value is not None and value != expected:
            check.fail('SCOPE-002', 'dut.' + field, f'frozen value is {expected} {unit.value}')
    if plan.dut.test_model not in tuple(TestModel):
        check.fail('PROC-003', 'dut.test_model', 'unsupported test model')


def _expected_filters():
    """Count of each (model, side, offset, bandwidth) filter that the near-channel grid requires."""
    expected = Counter()
    for tm in TestModel:
        for side in SIDES:
            for region in RULES['obue_mask']['regions']:
                if region['id'] == 'far_flat':
                    continue
                for offset in range(region['centre_offset_start_hz'], region['centre_offset_stop_exclusive_hz'],
                                    region['centre_step_hz']):
                    expected[(tm.value, side, offset, region['measurement_bandwidth_hz'])] += 1
    return expected


def _check_scan(plan, i, check, expected, actual, power_known):
    """Check one scan's settings and add the filters it covers to `actual`."""
    vcap = CAPABILITY['vendor_supported']
    cap = CAPABILITY['study_assumptions']
    scan = plan.scans[i]
    region, settings = scan.region, scan.analyzer
    loc = f'scans[{i}]'
    geometry = {name: check.quantity(getattr(region, name), Unit.HZ, loc + '.' + name, True)
                for name in REGION_FIELDS}
    levels = {name: check.quantity(getattr(settings, name), unit, loc + '.' + name, name in POSITIVE_ANALYZER_FIELDS)
              for name, unit in ANALYZER_FIELDS}
    if scan.test_model not in tuple(TestModel):
        check.fail('PROC-003', loc, 'unsupported test model')
    if region.side not in SIDES:
        check.fail('PROC-004', loc, 'unknown measurement side')
    if settings.detector != Detector.TRUE_RMS or settings.averaging_domain != AveragingDomain.POWER:
        check.fail('PROC-002', loc, 'requires True RMS and power-domain averaging')
    if any(x is None for x in (*geometry.values(), *levels.values())):
        return

    start, stop, step, bw = (geometry[name] for name in REGION_FIELDS)
    if start >= stop or (stop - start) / step > MAX_FILTERS_PER_SCAN or any(x != int(x) for x in (start, stop, step, bw)):
        check.fail('PROC-004', loc, 'invalid or excessive scan range')
        return

    # RF settings and headroom.
    rbw = levels['rbw']
    if not vcap['rbw_min_hz'] <= rbw <= vcap['rbw_max_hz']:
        check.fail('SAFE-001', loc + '.rbw', 'outside declared RBW range')
    if rbw != bw:
        check.fail('PROC-001', loc, 'RBW must equal measurement bandwidth in this study')
    if not isinstance(settings.preamp_enabled, bool):
        check.fail('IFACE-001', loc, 'state flags must be booleans')
    if not cap['attenuation_min_db'] <= levels['attenuation'] <= cap['attenuation_max_db']:
        check.fail('SAFE-001', loc + '.attenuation', 'outside virtual attenuation range')
    if not cap['reference_level_min_dbm'] <= levels['reference_level'] <= cap['reference_level_max_dbm']:
        check.fail('SAFE-001', loc + '.reference_level', 'outside virtual reference-level range')
    if power_known:
        pin = plan.actual_output_power_dbm - plan.external_loss_db
        mixer = pin - levels['attenuation'] + (cap['preamp_gain_db'] if settings.preamp_enabled else 0)
        if pin > cap['input_limit_dbm'] or mixer > cap['mixer_limit_dbm'] or pin > levels['reference_level']:
            check.fail('SAFE-001', loc, 'input, mixer or reference-level headroom exceeded')
    span_low = levels['center_frequency'] - levels['span'] / 2
    span_high = levels['center_frequency'] + levels['span'] / 2
    if span_low < vcap['frequency_min_hz'] or span_high > vcap['frequency_max_hz']:
        check.fail('SAFE-001', loc, 'span outside supported frequency range')

    # Grid coverage: every filter must be on the frozen grid and inside the span.
    tm = scan.test_model.value if isinstance(scan.test_model, TestModel) else str(scan.test_model)
    for offset in range(int(start), int(stop), int(step)):
        key = (tm, region.side, offset, int(bw))
        actual[key] += 1
        if key not in expected:
            check.fail('PROC-004', loc, 'filter center or bandwidth outside frozen grid')
            break
        center = filter_center_hz(region.side, offset)
        if center - bw / 2 < span_low or center + bw / 2 > span_high:
            check.fail('PROC-004', loc, 'analyzer span does not cover filter')
            break
