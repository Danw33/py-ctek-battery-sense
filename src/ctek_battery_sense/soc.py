"""State of Charge calculation compatible with CTEK Battery Sense."""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import BatteryStatus


@dataclass
class _Point:
    voltage: float
    soc: float = 0.0
    mode: int = 0


def battery_status(soc: float) -> BatteryStatus:
    """Return the monitor application's battery status category."""

    if soc >= 58:
        return "green"
    if soc >= 35:
        return "amber"
    return "red"


def _stable(points: Sequence[_Point], index: int) -> bool:
    return all(
        abs(points[left].voltage - points[left + 1].voltage) < 0.11
        for left in range(index - 3, index)
    )


def _slope(
    points: Sequence[_Point], index: int, discharge_samples: int, minutes: int
) -> float:
    if discharge_samples == 3:
        values = [points[index - 1].voltage, points[index].voltage]
    elif discharge_samples == 4:
        values = [
            points[index - 2].voltage,
            points[index - 1].voltage,
            points[index].voltage,
        ]
    else:
        values = [point.voltage for point in points[index - 3 : index + 1]]
    xs = [offset * minutes / 60 for offset in range(len(values))]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(values) / len(values)
    return sum(
        (x - x_mean) * (y - y_mean) for x, y in zip(xs, values, strict=True)
    ) / sum((x - x_mean) ** 2 for x in xs)


def _average(points: Sequence[_Point], index: int) -> float:
    values = points[max(0, index - 5) : index + 1]
    return sum(point.voltage for point in values) / len(values)


def calculate_soc(
    voltages: Iterable[float], capacity_ah: float = 75, sample_minutes: int = 5
) -> float:
    """Calculate the latest SoC using the official application's state machine."""

    if not 5 <= capacity_ah <= 200:
        raise ValueError("capacity must be between 5 and 200 Ah")
    if sample_minutes <= 0:
        raise ValueError("sample interval must be positive")
    points = [_Point(float(voltage)) for voltage in voltages]
    if not points:
        raise ValueError("at least one voltage sample is required")

    reference_ocv = 12.7
    rest_mode = True
    rest_start = -(10 * 60 // sample_minutes)
    discharge_mode = False
    previous_discharge = False
    discharge_samples = 0
    charge_mode = False
    estimated_current = 0.0

    for index, point in enumerate(points):
        voltage = point.voltage
        if index:
            previous = points[index - 1]
            discharge_mode = previous_discharge
            previous_discharge = False
            if (
                voltage < 12.7
                and previous.voltage < 13.2
                and previous.voltage - voltage > 0.07
                and not discharge_mode
            ):
                discharge_mode = True
                discharge_samples = 0
                charge_mode = False
                rest_mode = False
            if voltage > 13.2:
                charge_mode = True
                rest_mode = False
            else:
                if not rest_mode:
                    rest_start = index
                charge_mode = False
                rest_mode = True

        if (
            index >= 5
            and voltage < 12.6
            and _stable(points, index)
            and (slope := _slope(points, index, 5, sample_minutes) * 1000 / 3600)
            < -1e-6
            and voltage < 12.9
            and point.mode != 5
        ):
            resistance = (
                math.exp(points[index - 1].soc * -6.25 / 100) * 1.25 + 0.25
            ) / slope
            estimated_current = (capacity_ah / resistance) / (
                math.exp(-(sample_minutes * 4) / 20) + 1
            )
            point.soc = (
                estimated_current * 100 * sample_minutes / (capacity_ah * 60)
                + points[index - 1].soc
            )
            point.soc = max(0, point.soc)
            reference_ocv = point.soc / 78 + 11.55
            discharge_mode = True
            rest_mode = charge_mode = False
            point.mode = 6
            if estimated_current > -capacity_ah / 100:
                estimated_current = 0
                discharge_mode = previous_discharge = False
                rest_mode = True
                discharge_samples = 0
                point.soc = points[index - 1].soc
                point.mode = 5

        if (
            index >= 5
            and voltage < 13.2
            and _stable(points, index)
            and (slope := _slope(points, index, 5, sample_minutes) * 1000 / 3600) > 1e-6
        ):
            estimated_current = capacity_ah / (0.8 / slope)
            point.soc = (
                estimated_current * 100 * sample_minutes / (60 * capacity_ah)
                + points[index - 1].soc
            )
            point.soc = min(100, point.soc)
            reference_ocv = point.soc / 78 + 11.55
            discharge_mode = rest_mode = False
            charge_mode = True
            point.mode = 7
            if estimated_current < capacity_ah / 100:
                charge_mode = discharge_mode = False
                discharge_samples = 0
                rest_mode = True
                point.soc = points[index - 1].soc
                estimated_current = 0
                point.mode = 5

        if charge_mode and voltage >= 13.2 and index:
            previous_soc = points[index - 1].soc
            current = math.sqrt(max(0, 1 - (previous_soc / 100) ** 2)) * 6
            point.soc = (
                current / capacity_ah * (sample_minutes / 60) * 100 + previous_soc
            )
            reference_ocv = point.soc / 78 + 11.55
            point.mode = 1

        if rest_mode and point.mode < 6:
            rest_hours = (index - rest_start) / (60 / sample_minutes)
            if rest_hours < 5:
                point.soc = (reference_ocv - 11.55) * 78
                point.mode = 3
            elif rest_hours < 10:
                blend = 1 - ((index - (rest_start + 60)) / 60)
                average = _average(points, index)
                point.soc = ((1 - blend) * average + reference_ocv * blend - 11.55) * 78
                point.mode = 4
            else:
                reference_ocv = _average(points, index)
                point.soc = (reference_ocv - 11.55) * 78
                point.mode = 5

        if discharge_mode and point.mode < 6 and index:
            previous = points[index - 1]
            if voltage < previous.voltage + 0.05:
                discharge_samples += 1
                previous_discharge = True
                if discharge_samples < 3:
                    estimated_current = 0
                    point.soc = previous.soc
                    reference_ocv = point.soc / 78 + 11.55
                else:
                    slope = (
                        _slope(points, index, discharge_samples, sample_minutes)
                        * 1000
                        / 3600
                    )
                    if slope < -1e-6:
                        resistance = (
                            math.exp(previous.soc * -6.25 / 100) * 1.25 + 0.25
                        ) / slope
                        relaxation = (
                            math.exp(-(discharge_samples * sample_minutes) / 20) + 1
                        )
                        estimated_current = max(
                            -100, (capacity_ah / resistance) / relaxation
                        )
                        if estimated_current > -capacity_ah / 100:
                            estimated_current = 0
                            discharge_mode = previous_discharge = False
                        if discharge_samples == 3:
                            points[index - 2].soc = (
                                estimated_current
                                * 0.5
                                * 100
                                * sample_minutes
                                / (60 * capacity_ah)
                                + points[index - 3].soc
                            )
                            previous.soc = (
                                estimated_current
                                * 0.75
                                * 100
                                * sample_minutes
                                / (60 * capacity_ah)
                                + points[index - 2].soc
                            )
                        point.soc = (
                            estimated_current
                            * 100
                            * sample_minutes
                            / (60 * capacity_ah)
                            + previous.soc
                        )
                        reference_ocv = point.soc / 78 + 11.55
                        if estimated_current > -capacity_ah / 100:
                            discharge_samples = 0
                    else:
                        discharge_mode = previous_discharge = False
                        discharge_samples = 0
                        point.soc = previous.soc
            else:
                discharge_mode = previous_discharge = False
                discharge_samples = 0
                point.soc = previous.soc

        point.soc = min(100, max(0, point.soc))

    return points[-1].soc
