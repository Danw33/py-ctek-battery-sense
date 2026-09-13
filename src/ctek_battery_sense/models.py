"""Data models for CTEK Battery Sense."""

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

BatteryStatus: TypeAlias = Literal["green", "amber", "red"]


@dataclass
class CTEKHistory:
    """Voltage history used by the State of Charge calculation."""

    cursor: int | None = None
    sample_interval: int | None = None
    voltages: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class CTEKReadResult:
    """Current readings and synchronization metadata."""

    voltage: float
    temperature: float
    uptime: int
    sample_interval: int
    cursor: int
