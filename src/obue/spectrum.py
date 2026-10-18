"""Piecewise constant PSD (mW/Hz) plus ideal tones (mW).

Measurement filters are rectangular with ENBW equal to their width.
"""
from bisect import bisect_left
from dataclasses import dataclass
from math import isfinite, log10


def dbm_to_mw(dbm):
    if not isfinite(dbm):
        raise ValueError('power must be finite')
    return 10 ** (dbm / 10)


def mw_to_dbm(mw):
    if not isfinite(mw) or mw <= 0:
        raise ValueError('power must be finite and positive')
    return 10 * log10(mw)


@dataclass(frozen=True)
class Spectrum:
    edges_hz: tuple[float, ...]
    psd_mw_hz: tuple[float, ...]
    tones: tuple[tuple[float, float], ...] = ()

    def __post_init__(self):
        edges = self.edges_hz
        if len(edges) != len(self.psd_mw_hz) + 1 or not self.psd_mw_hz:
            raise ValueError('N densities require N+1 edges')
        if not all(isfinite(x) for x in edges) or any(a >= b for a, b in zip(edges, edges[1:])):
            raise ValueError('edges must be finite and strictly increasing')
        if any(not isfinite(p) or p < 0 for p in self.psd_mw_hz):
            raise ValueError('PSD must be finite and nonnegative')
        for frequency, power in self.tones:
            if (not isfinite(frequency) or not isfinite(power) or power < 0
                    or not edges[0] <= frequency < edges[-1]):
                raise ValueError('invalid tone')

    def power_mw(self, low, high):
        """Production integrator: sum interval overlaps, treating windows as [low,high)."""
        if not isfinite(low) or not isfinite(high) or low >= high:
            raise ValueError('invalid window')
        if low < self.edges_hz[0] or high > self.edges_hz[-1]:
            raise ValueError('window outside spectrum support')
        total = 0.0
        first = max(0, bisect_left(self.edges_hz, low) - 1)
        for i in range(first, len(self.psd_mw_hz)):
            a, b = self.edges_hz[i:i + 2]
            if a >= high:
                break
            total += self.psd_mw_hz[i] * max(0, min(b, high) - max(a, low))
        return total + sum(p for f, p in self.tones if low <= f < high)

