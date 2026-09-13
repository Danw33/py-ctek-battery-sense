"""Tests for CTEK State of Charge and RAG."""

import pytest

from ctek_battery_sense.soc import battery_status, calculate_soc


def test_rested_voltage() -> None:
    """A first sample is treated as fully rested by the official app."""

    assert calculate_soc([12.33642578125]) == pytest.approx(61.3412109375)


def test_rag_boundaries() -> None:
    """RAG uses unrounded fixed thresholds."""

    assert battery_status(58) == "green"
    assert battery_status(57.999) == "amber"
    assert battery_status(35) == "amber"
    assert battery_status(34.999) == "red"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"voltages": [12.5], "capacity_ah": 4}, "capacity"),
        ({"voltages": [12.5], "capacity_ah": 201}, "capacity"),
        ({"voltages": [12.5], "sample_minutes": 0}, "sample interval"),
        ({"voltages": []}, "at least one"),
    ],
)
def test_invalid_inputs(kwargs: dict, message: str) -> None:
    """Invalid capacity, interval and history inputs are rejected."""

    with pytest.raises(ValueError, match=message):
        calculate_soc(**kwargs)


@pytest.mark.parametrize(
    ("voltages", "expected"),
    [
        ([12.50, 12.49, 12.48, 12.47, 12.46, 12.45], 71.76911069582793),
        ([12.5 - index * 0.00001 for index in range(6)], 74.09805),
        ([12.40, 12.41, 12.42, 12.43, 12.44, 12.45], 68.20722222222216),
        ([12.4 + index * 0.00001 for index in range(6)], 66.30195),
        ([12.7, 13.3, 13.4, 13.5], 90.57198557756672),
        ([13.4, *([12.5] * 121)], 74.1),
        ([12.7, 12.5, 12.49, 12.48, 12.47], 87.23663457856958),
        ([12.7, 12.5, 12.49, 12.48, 12.6], 88.03268863239043),
        ([12.7, 12.5, 12.49, 12.52], 89.7),
        ([12.7, 12.5, 12.49999, 12.49998], 89.7),
    ],
    ids=[
        "stable-discharge",
        "negligible-discharge",
        "stable-recovery",
        "negligible-recovery",
        "active-charge",
        "ten-hour-rest",
        "discharge-estimate",
        "load-removed",
        "positive-slope-during-discharge",
        "negligible-current-during-discharge",
    ],
)
def test_state_machine_scenarios(voltages: list[float], expected: float) -> None:
    """Representative voltage histories preserve state-machine behaviour."""

    assert calculate_soc(voltages) == pytest.approx(expected)
