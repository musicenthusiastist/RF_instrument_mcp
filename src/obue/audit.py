"""Private, separately implemented eligibility audit of serialized event snapshots.

No calls to the condition-C validator. Duplicate logic is intentional here: test
fixtures reconcile both implementations, rather than scoring a gate with itself.
"""
from collections import Counter
from math import isfinite
from .config import CAPABILITY

PROFILE_ID = 'n78_wa_cat_a_100m_30k_midchannel_r18'
MODELS = ('NR-FR1-TM1.1', 'NR-FR1-TM1.2')
SIDES = ('lower', 'upper')
HZ_PER_UNIT = {'Hz': 1, 'kHz': 1000, 'MHz': 1000000, 'GHz': 1000000000}


def audit_plan(data, scan_index=None):
    """Return the rule IDs a serialized plan violates, in first-occurrence order.

    With scan_index, audit one scan's acquisition eligibility; otherwise also
    require exact full-grid coverage. Any malformed field yields IFACE-001.
    """
    errors = []

    def add(rule):
        if rule not in errors:
            errors.append(rule)

    try:
        _audit_profile(data, add)
        expected = _expected_filters(data['scope'])
        actual = Counter()
        scans = data['scans'] if scan_index is None else [data['scans'][scan_index]]
        for scan in scans:
            _audit_scan(scan, data, add, expected, actual)
        if scan_index is None:
            if expected != actual:
                add('PROC-004')
            if {s['test_model'] for s in scans} != set(MODELS):
                add('PROC-005')
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        add('IFACE-001')
    return errors


def _value(obj, kind):
    """Read a {value, unit} quantity: kind 'Hz' accepts any frequency unit, else the exact unit."""
    unit = obj['unit']
    value = obj['value']
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError()
    if kind == 'Hz' and unit in HZ_PER_UNIT:
        return value * HZ_PER_UNIT[unit]
    if kind == unit:
        return value
    raise ValueError()


def _audit_profile(data, add):
    if data['profile_id'] != PROFILE_ID:
        add('SCOPE-003')
    dut = data['dut']
    if dut['band'] != 'n78':
        add('SCOPE-001')
    if (dut['bs_type'], dut['bs_class'], dut['emission_category']) != ('1-C', 'wide_area', 'A'):
        add('SCOPE-003')
    frozen = (_value(dut['carrier_center'], 'Hz'), _value(dut['channel_bandwidth'], 'Hz'),
              _value(dut['subcarrier_spacing'], 'Hz'), _value(dut['rated_output_power'], 'dBm'))
    if frozen != (3550e6, 100e6, 30e3, 46):
        add('SCOPE-002')
    if data['actual_output_power_dbm'] != 46:
        add('PROC-003')
    if data['external_loss_db'] != 40:
        add('SAFE-001')
    if data['scope'] not in ('full_analytical', 'near_channel_nr'):
        add('SCOPE-003')


def _expected_filters(scope):
    """Four combinations of model and side, with 330 (or 100 near-channel) positions each."""
    near = [50000 + 100000 * j for j in range(100)]
    far = [10500000 + 1000000 * j for j in range(230)] if scope == 'full_analytical' else []
    expected = Counter()
    for model in MODELS:
        for side in SIDES:
            for offset in near + far:
                expected[(model, side, offset, 100000 if offset < 10000000 else 1000000)] += 1
    return expected


def _audit_scan(scan, data, add, expected, actual):
    caps = CAPABILITY['study_assumptions']
    vendor = CAPABILITY['vendor_supported']
    region = scan['region']
    settings = scan['analyzer']
    side = region['side']
    model = scan['test_model']
    if model not in MODELS:
        add('PROC-003')
    if side not in SIDES:
        add('PROC-004')
    lo = _value(region['start_offset'], 'Hz')
    hi = _value(region['stop_offset'], 'Hz')
    step = _value(region['step'], 'Hz')
    bw = _value(region['measurement_bandwidth'], 'Hz')
    rbw = _value(settings['rbw'], 'Hz')
    vbw = _value(settings['vbw'], 'Hz')
    center = _value(settings['center_frequency'], 'Hz')
    span = _value(settings['span'], 'Hz')
    att = _value(settings['attenuation'], 'dB')
    ref = _value(settings['reference_level'], 'dBm')

    if settings['detector'] != 'true_rms' or settings['averaging_domain'] != 'power':
        add('PROC-002')
    if not vendor['rbw_min_hz'] <= rbw <= vendor['rbw_max_hz']:
        add('SAFE-001')
    if rbw != bw:
        add('PROC-001')
    if (not caps['attenuation_min_db'] <= att <= caps['attenuation_max_db']
            or not caps['reference_level_min_dbm'] <= ref <= caps['reference_level_max_dbm']):
        add('SAFE-001')
    if not isinstance(settings['preamp_enabled'], bool):
        add('IFACE-001')
    pin = data['actual_output_power_dbm'] - data['external_loss_db']
    preamp_gain = caps['preamp_gain_db'] if settings['preamp_enabled'] else 0
    if pin > caps['input_limit_dbm'] or pin > ref or pin - att + preamp_gain > caps['mixer_limit_dbm']:
        add('SAFE-001')
    if bw <= 0 or vbw <= 0 or span <= 0:
        add('SCOPE-002')
        return
    if center - span / 2 < vendor['frequency_min_hz'] or center + span / 2 > vendor['frequency_max_hz']:
        add('SAFE-001')
    if lo >= hi or step <= 0 or (hi - lo) / step > 2000 or any(v != int(v) for v in (lo, hi, step, bw)):
        add('PROC-004')
        return

    for offset in range(int(lo), int(hi), int(step)):
        key = (model, side, offset, int(bw))
        actual[key] += 1
        if key not in expected:
            add('PROC-004')
        f = (3500e6 - offset) if side == 'lower' else (3600e6 + offset)
        if f - bw / 2 < center - span / 2 or f + bw / 2 > center + span / 2:
            add('PROC-004')
