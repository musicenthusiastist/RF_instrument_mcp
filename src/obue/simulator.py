"""Stateful segmented analyzer; models are explicit study assumptions.

The backend logs invalid executions but does not prevent them. Semantic gating
belongs to condition C. It never imports private scenario labels or reference.py.
"""
from dataclasses import asdict, replace
import hashlib
import json

from .config import CAPABILITY
from .domain import Unit, filter_center_hz
from .plan import plan_to_dict, replace_scan
from .spectrum import mw_to_dbm
from .validation import validate_plan

MAX_FILTERS_PER_SCAN = 2000


class Analyzer:
    def __init__(self, plan, spectrum):
        if not isinstance(spectrum, dict) or not {s.test_model.value for s in plan.scans} <= set(spectrum):
            raise ValueError('spectrum must map every test model in the plan to its NR Spectrum')
        self.initial_plan = plan
        self.plan = plan
        self.spectrum = spectrum  # {test model: Spectrum}
        self.events = []          # every configure/acquire attempt, executed or not
        self.results = {}         # scan index -> latest acquisition
        self.revision = 0

    def snapshot(self):
        return hashlib.sha256(json.dumps(plan_to_dict(self.plan), sort_keys=True).encode()).hexdigest()

    def state(self):
        return {'instrument_family': CAPABILITY['instrument_family'],
                'analyzer_profile_id': CAPABILITY['profile_id'],
                'plan': plan_to_dict(self.plan), 'state_snapshot_id': self.snapshot()}

    def configure(self, index, changes, *, enforce=False):
        """index=None applies the changes to every scan (global instrument state, as in SCPI)."""
        if index is None:
            indices = range(len(self.plan.scans))
        else:
            self._check_index(index)
            indices = [index]
        candidate = self.plan
        for i in indices:
            candidate = replace_scan(candidate, i, analyzer=replace(candidate.scans[i].analyzer, **changes))
        problems = validate_plan(candidate, scan_index=index)
        executed = not (enforce and problems)
        event = {'operation': 'configure', 'scan_index': index, 'before': self.snapshot(),
                 'candidate_plan': plan_to_dict(candidate),
                 'violations': [asdict(v) for v in problems], 'executed': executed}
        if executed:
            self.plan = candidate
            self.revision += 1
            for i in indices:
                self.results.pop(i, None)  # New settings invalidate the old trace.
        event['after'] = self.snapshot()
        self.events.append(event)
        if not executed:
            return {'blocked': True, 'violations': event['violations']}
        return {'configured': True, 'state_snapshot_id': event['after']}

    def acquire(self, index, *, enforce=False):
        self._check_index(index)
        problems = validate_plan(self.plan, scan_index=index)
        executed = not (enforce and problems)
        event = {'operation': 'acquire', 'scan_index': index, 'state': self.snapshot(),
                 'acquisition_plan': plan_to_dict(self.plan),
                 'violations': [asdict(v) for v in problems], 'executed': executed}
        self.events.append(event)
        if not executed:
            return {'blocked': True, 'violations': event['violations']}
        try:
            result = self._measure(index)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            event['error'] = str(exc)
            return {'error': str(exc)}
        self.results[index] = result
        event['rows'] = len(result['readings'])
        event['simulated_time_s'] = result['simulated_time_s']
        return result

    def _check_index(self, index):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.plan.scans):
            raise ValueError('invalid scan index')

    def _overload_db(self, settings):
        """Modeled compression: the worst excess over input, mixer or reference-level headroom."""
        cap = CAPABILITY['study_assumptions']
        pin = self.plan.actual_output_power_dbm - self.plan.external_loss_db
        mixer = pin - settings.attenuation.to(Unit.DB).value + (cap['preamp_gain_db'] if settings.preamp_enabled else 0)
        return max(0, mixer - cap['mixer_limit_dbm'], pin - cap['input_limit_dbm'],
                   pin - settings.reference_level.to(Unit.DBM).value)

    def _measure(self, index):
        scan = self.plan.scans[index]
        region, settings = scan.region, scan.analyzer
        start = region.start_offset.to(Unit.HZ).value
        stop = region.stop_offset.to(Unit.HZ).value
        step = region.step.to(Unit.HZ).value
        if step <= 0 or stop <= start or (stop - start) / step > MAX_FILTERS_PER_SCAN:
            raise ValueError('unexecutable scan geometry')
        bw = region.measurement_bandwidth.to(Unit.HZ).value
        rbw = settings.rbw.to(Unit.HZ).value
        if bw <= 0 or rbw <= 0:
            raise ValueError('nonpositive bandwidth')

        loss = self.plan.external_loss_db
        overload = self._overload_db(settings)
        bias = _detector_bias_db(settings)
        center_frequency = settings.center_frequency.to(Unit.HZ).value
        half_span = settings.span.to(Unit.HZ).value / 2
        span_low, span_high = center_frequency - half_span, center_frequency + half_span
        spectrum = self.spectrum[scan.test_model.value]

        readings = []
        for offset in range(int(start), int(stop), int(step)):
            # The analyzer integrates over its RBW, which may differ from the required bandwidth.
            center = filter_center_hz(region.side, offset)
            low, high = center - rbw / 2, center + rbw / 2
            if low < span_low or high > span_high:
                raise ValueError('measurement outside configured span')
            incident = spectrum.power_mw(low, high) * 10 ** (-loss / 10)
            # Fixed path correction is always applied once, inside the backend.
            value = mw_to_dbm(incident) + loss - overload + bias
            readings.append({'test_model': scan.test_model.value, 'side': region.side, 'offset_hz': offset,
                             'frequency_hz': center, 'value': value, 'unit': 'dBm',
                             'measurement_bandwidth_hz': bw, 'effective_bandwidth_hz': rbw})
        return {'readings': readings, 'reference_plane': 'transmitter',
                'state_snapshot_id': self.snapshot(), 'scan_index': index, 'overload': overload > 0,
                'simulated_time_s': len(readings) * max(.001, 10 / rbw)}


def _detector_bias_db(settings):
    """Study-assumed level error from a non-RMS detector or log-domain averaging."""
    cap = CAPABILITY['study_assumptions']
    bias = 0.0
    if settings.detector.value == 'peak':
        bias += cap['peak_detector_bias_db']
    elif settings.detector.value == 'sample':
        bias += cap['sample_detector_bias_db']
    if settings.averaging_domain.value == 'log':
        bias += cap['log_average_bias_db']
    return bias
