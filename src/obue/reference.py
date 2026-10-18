"""Private reference evaluator. Never imported by the instrument or MCP server.

Its power algorithm uses local antiderivatives across PSD knots, separately
implemented from Spectrum.power_mw. MATLAB additionally verifies exported cases.
"""
from bisect import bisect_right
from math import log10

MODELS = ('NR-FR1-TM1.1', 'NR-FR1-TM1.2')
# (first centre offset, stop offset exclusive, step, filter bandwidth) in Hz.
REGIONS = ((50000, 5050000, 100000, 100000), (5050000, 10050000, 100000, 100000))


def reference_power_mw(spectrum, low, high):
    if not spectrum.edges_hz[0] <= low < high <= spectrum.edges_hz[-1]:
        raise ValueError('invalid reference interval')
    # Breakpoint quadrature is exact for the declared constant-density segments.
    points = [low] + [e for e in spectrum.edges_hz if low < e < high] + [high]
    total = 0.0
    for left, right in zip(points, points[1:]):
        mid = (left + right) / 2
        total += (right - left) * spectrum.psd_mw_hz[bisect_right(spectrum.edges_hz, mid) - 1]
    for frequency, power in spectrum.tones:
        if low <= frequency < high:
            total += power
    return total


def reference_limit(offset):
    """Independent expression of the frozen mask; no production lookup call."""
    if 50000 <= offset < 5050000:
        return -7 - 7 * (offset - 50000) / 5000000
    if 5050000 <= offset < 10050000:
        return -14.0
    if 10500000 <= offset < 240000000:
        return -13.0
    raise ValueError('offset outside mask')


def reference_decision(y, limit):
    return 'pass' if y <= limit else 'fail'


def aggregate(decisions):
    values = list(decisions)
    if not values or any(v not in ('pass', 'fail') for v in values):
        raise ValueError('invalid decision set')
    return 'fail' if 'fail' in values else 'pass'


def evaluate_reference(spectra):
    """Ideal per-filter powers and the true overall decision on the near-channel grid."""
    rows = []
    for model in MODELS:
        source = spectra[model]
        for side in ('lower', 'upper'):
            for start, stop, step, bw in REGIONS:
                for offset in range(start, stop, step):
                    center = 3500e6 - offset if side == 'lower' else 3600e6 + offset
                    power = 10 * log10(reference_power_mw(source, center - bw / 2, center + bw / 2))
                    limit = reference_limit(offset)
                    rows.append({'test_model': model, 'side': side, 'offset_hz': offset, 'power_dbm': power,
                                 'limit_dbm': limit, 'decision': reference_decision(power, limit)})
    return {'decision': aggregate(r['decision'] for r in rows), 'rows': rows}
