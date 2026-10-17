"""Interface-independent domain objects and initial OBUE oracle functions.

The SCPI and MCP adapters will depend on this module. This module must not
depend on either interface so every experimental arm shares one state model and
one hidden oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Iterable


class Dimension(str, Enum):
    FREQUENCY = "frequency"
    TIME = "time"
    ABSOLUTE_POWER = "absolute_power"
    LEVEL_RATIO = "level_ratio"


class Unit(str, Enum):
    HZ = "Hz"
    KHZ = "kHz"
    MHZ = "MHz"
    GHZ = "GHz"
    SECOND = "s"
    MILLISECOND = "ms"
    DBM = "dBm"
    DB = "dB"


_UNIT_DIMENSION = {
    Unit.HZ: Dimension.FREQUENCY,
    Unit.KHZ: Dimension.FREQUENCY,
    Unit.MHZ: Dimension.FREQUENCY,
    Unit.GHZ: Dimension.FREQUENCY,
    Unit.SECOND: Dimension.TIME,
    Unit.MILLISECOND: Dimension.TIME,
    Unit.DBM: Dimension.ABSOLUTE_POWER,
    Unit.DB: Dimension.LEVEL_RATIO,
}

_LINEAR_SCALE_TO_BASE = {
    Unit.HZ: 1.0,
    Unit.KHZ: 1e3,
    Unit.MHZ: 1e6,
    Unit.GHZ: 1e9,
    Unit.SECOND: 1.0,
    Unit.MILLISECOND: 1e-3,
}


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: Unit

    def __post_init__(self) -> None:
        if not isinstance(self.unit, Unit):
            raise ValueError("unit must be a Unit enum")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("quantity value must be numeric")
        if not isfinite(self.value):
            raise ValueError("quantity value must be finite")

    @property
    def dimension(self) -> Dimension:
        return _UNIT_DIMENSION[self.unit]

    def to(self, target: Unit) -> "Quantity":
        if self.dimension != _UNIT_DIMENSION[target]:
            raise ValueError(
                f"cannot convert {self.dimension.value} to "
                f"{_UNIT_DIMENSION[target].value}"
            )
        if self.unit == target:
            return self
        if self.unit not in _LINEAR_SCALE_TO_BASE or target not in _LINEAR_SCALE_TO_BASE:
            raise ValueError("logarithmic units require an explicit physical conversion")
        base_value = self.value * _LINEAR_SCALE_TO_BASE[self.unit]
        return Quantity(base_value / _LINEAR_SCALE_TO_BASE[target], target)


class Detector(str, Enum):
    TRUE_RMS = "true_rms"
    PEAK = "peak"
    SAMPLE = "sample"


class AveragingDomain(str, Enum):
    POWER = "power"
    LOG = "log"
    NONE = "none"


class TestModel(str, Enum):
    TM1_1 = "NR-FR1-TM1.1"
    TM1_2 = "NR-FR1-TM1.2"


class ConformityDecision(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class DUTState:
    band: str
    bs_type: str
    bs_class: str
    emission_category: str
    carrier_center: Quantity
    channel_bandwidth: Quantity
    subcarrier_spacing: Quantity
    rated_output_power: Quantity
    test_model: TestModel

    @property
    def channel_lower_edge_hz(self) -> float:
        return (
            self.carrier_center.to(Unit.HZ).value
            - self.channel_bandwidth.to(Unit.HZ).value / 2.0
        )

    @property
    def channel_upper_edge_hz(self) -> float:
        return (
            self.carrier_center.to(Unit.HZ).value
            + self.channel_bandwidth.to(Unit.HZ).value / 2.0
        )


@dataclass(frozen=True)
class AnalyzerState:
    center_frequency: Quantity
    span: Quantity
    rbw: Quantity
    vbw: Quantity
    detector: Detector
    averaging_domain: AveragingDomain
    attenuation: Quantity
    reference_level: Quantity
    preamp_enabled: bool


@dataclass(frozen=True)
class ScanRegion:
    side: str
    start_offset: Quantity
    stop_offset: Quantity
    step: Quantity
    measurement_bandwidth: Quantity


def frozen_dut_state(test_model: TestModel = TestModel.TM1_1) -> DUTState:
    return DUTState(
        band="n78",
        bs_type="1-C",
        bs_class="wide_area",
        emission_category="A",
        carrier_center=Quantity(3550.0, Unit.MHZ),
        channel_bandwidth=Quantity(100.0, Unit.MHZ),
        subcarrier_spacing=Quantity(30.0, Unit.KHZ),
        rated_output_power=Quantity(46.0, Unit.DBM),
        test_model=test_model,
    )


CHANNEL_LOWER_EDGE_HZ = 3500e6
CHANNEL_UPPER_EDGE_HZ = 3600e6


def filter_center_hz(side: str, offset_hz: float) -> float:
    """Return the absolute filter centre for an offset from the nearest channel edge."""

    if side == "lower":
        return CHANNEL_LOWER_EDGE_HZ - offset_hz
    return CHANNEL_UPPER_EDGE_HZ + offset_hz


def obue_limit_dbm(f_offset_hz: float) -> float:
    """Return the frozen Category A upper limit for a valid filter centre.

    The centre offset is relative to the nearest channel edge. The function
    intentionally rejects the 10.05-10.5 MHz interval because no valid filter
    centre lies there when the measurement bandwidth changes from 100 kHz to
    1 MHz.
    """

    if not isfinite(f_offset_hz):
        raise ValueError("f_offset_hz must be finite")
    f_mhz = f_offset_hz / 1e6
    if 0.05 <= f_mhz < 5.05:
        return -7.0 - 1.4 * (f_mhz - 0.05)
    if 5.05 <= f_mhz < 10.05:
        return -14.0
    if 10.5 <= f_mhz < 240.0:
        return -13.0
    raise ValueError("offset is outside a valid frozen-profile filter centre")


def obue_measurement_bandwidth_hz(f_offset_hz: float) -> float:
    """Return the required measurement bandwidth for a valid centre offset."""

    f_mhz = f_offset_hz / 1e6
    if 0.05 <= f_mhz < 10.05:
        return 100e3
    if 10.5 <= f_mhz < 240.0:
        return 1e6
    raise ValueError("offset is outside a valid frozen-profile filter centre")


def _offset_range(start_hz: int, stop_exclusive_hz: int, step_hz: int) -> Iterable[int]:
    if step_hz <= 0:
        raise ValueError("step_hz must be positive")
    return range(start_hz, stop_exclusive_hz, step_hz)


def expected_obue_offsets_hz() -> tuple[int, ...]:
    """Return valid centre offsets for one side of the frozen profile."""

    near_sloped = _offset_range(50_000, 5_050_000, 100_000)
    near_flat = _offset_range(5_050_000, 10_050_000, 100_000)
    far_flat = _offset_range(10_500_000, 240_000_000, 1_000_000)
    return tuple((*near_sloped, *near_flat, *far_flat))


def decision_for_upper_limit(
    measured_power_dbm: float,
    limit_dbm: float,
) -> ConformityDecision:
    """Apply the deterministic upper-limit policy; equality passes."""

    values = (measured_power_dbm, limit_dbm)
    if not all(isfinite(value) for value in values):
        raise ValueError("decision inputs must be finite")
    if measured_power_dbm <= limit_dbm:
        return ConformityDecision.PASS
    return ConformityDecision.FAIL
