"""Full plans use a state snapshot per scan, including both required test models."""
from dataclasses import asdict, dataclass, replace

from .config import RULES
from .domain import (AnalyzerState, AveragingDomain, Detector,
                     DUTState, Quantity, ScanRegion, TestModel, Unit, frozen_dut_state)


@dataclass(frozen=True)
class Scan:
    test_model: TestModel
    region: ScanRegion
    analyzer: AnalyzerState


@dataclass(frozen=True)
class Plan:
    profile_id: str
    dut: DUTState
    scans: tuple[Scan, ...]
    external_loss_db: float
    actual_output_power_dbm: float
    scope: str = 'full_analytical'


def default_plan(scope='full_analytical') -> Plan:
    if scope not in ('full_analytical', 'near_channel_nr'):
        raise ValueError('unknown measurement scope')
    span_mhz = 120 if scope == 'near_channel_nr' else 580  # covers every filter in scope
    scans = []
    for model in TestModel:
        for side in ('lower', 'upper'):
            for r in RULES['obue_mask']['regions']:
                if scope == 'near_channel_nr' and r['id'] == 'far_flat':
                    continue
                region = ScanRegion(side, Quantity(r['centre_offset_start_hz'], Unit.HZ),
                                    Quantity(r['centre_offset_stop_exclusive_hz'], Unit.HZ),
                                    Quantity(r['centre_step_hz'], Unit.HZ),
                                    Quantity(r['measurement_bandwidth_hz'], Unit.HZ))
                # A correct baseline: RBW = VBW = measurement bandwidth, RMS, power averaging.
                state = AnalyzerState(center_frequency=Quantity(3550, Unit.MHZ), span=Quantity(span_mhz, Unit.MHZ),
                                      rbw=region.measurement_bandwidth, vbw=region.measurement_bandwidth,
                                      detector=Detector.TRUE_RMS, averaging_domain=AveragingDomain.POWER,
                                      attenuation=Quantity(10, Unit.DB), reference_level=Quantity(10, Unit.DBM),
                                      preamp_enabled=False)
                scans.append(Scan(model, region, state))
    return Plan(RULES['metadata']['profile_id'], frozen_dut_state(), tuple(scans),
                external_loss_db=40, actual_output_power_dbm=46, scope=scope)


def plan_to_dict(plan):
    """JSON-compatible dict (enums become their string values)."""
    import json
    return json.loads(json.dumps(asdict(plan)))


def plan_from_dict(data):
    def q(x):
        return Quantity(x['value'], Unit(x['unit']))

    dut = dict(data['dut'])
    for name in ('carrier_center', 'channel_bandwidth', 'subcarrier_spacing', 'rated_output_power'):
        dut[name] = q(dut[name])
    dut['test_model'] = TestModel(dut['test_model'])
    scans = []
    for s in data['scans']:
        r = dict(s['region'])
        a = dict(s['analyzer'])
        for name in ('start_offset', 'stop_offset', 'step', 'measurement_bandwidth'):
            r[name] = q(r[name])
        for name in ('center_frequency', 'span', 'rbw', 'vbw', 'attenuation', 'reference_level'):
            a[name] = q(a[name])
        a['detector'] = Detector(a['detector'])
        a['averaging_domain'] = AveragingDomain(a['averaging_domain'])
        scans.append(Scan(TestModel(s['test_model']), ScanRegion(**r), AnalyzerState(**a)))
    other = {k: v for k, v in data.items() if k not in ('dut', 'scans')}
    return Plan(**other, dut=DUTState(**dut), scans=tuple(scans))


def replace_scan(plan, index, **changes):
    scans = list(plan.scans)
    scans[index] = replace(scans[index], **changes)
    return replace(plan, scans=tuple(scans))
