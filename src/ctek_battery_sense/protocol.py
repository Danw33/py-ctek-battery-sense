"""CTEK Battery Sense BLE protocol codecs."""

import hashlib
import re
import struct
from dataclasses import dataclass

SERVICE_UUID = "812ca8ed-a177-4d74-aefa-70098c5416af"
UPTIME_UUID = "337ea83e-744b-4b7c-b23f-45beaab11db7"
LIVE_VOLTAGE_UUID = "631994f8-fd2e-48bf-af8f-e86320e17d94"
LIVE_TEMPERATURE_UUID = "b5634099-97c4-4f37-adf3-771246e77754"
HISTORY_REQUEST_UUID = "418d1817-f516-45f1-ab64-433ae596613d"
HISTORY_DATA_UUID = "a337b4eb-2544-4f94-87c9-f7732e5fe250"
HISTORY_CURSOR_UUID = "fc3119da-9b40-414e-a1c4-6c1737718cae"
UNLOCK_UUID = "a1d67c01-fe3f-01bf-7d49-7dbdc43b02a6"
SAMPLE_INTERVAL_UUID = "e5a7e9a0-e2d7-4f46-83d1-0e55d2427780"

PRODUCT_PREFIX = "40149"
HISTORY_CAPACITY = 30_000
_UNLOCK_PREFIX = bytes.fromhex("e1da7195eced40e4bc16559e9de73ba4")


@dataclass(frozen=True)
class HistorySample:
    """One stored CTEK sample."""

    voltage: float
    temperature: float


def full_barcode(value: str) -> str:
    """Normalize and validate a Sender ID or full barcode."""

    normalized = re.sub(r"\s+", "", value).upper()
    if len(normalized) == 11:
        normalized = PRODUCT_PREFIX + normalized
    if len(normalized) != 16 or not normalized.isascii() or not normalized.isalnum():
        raise ValueError("invalid_sender_id")
    return normalized


def normalize_sender_id(value: str) -> str:
    """Return the normalized 11-character Sender ID."""

    return full_barcode(value)[len(PRODUCT_PREFIX) :]


def unlock_value(value: str) -> bytes:
    """Calculate the application unlock value."""

    return hashlib.md5(_UNLOCK_PREFIX + full_barcode(value).encode("ascii")).digest()


def decode_voltage(value: bytes | bytearray) -> float:
    """Decode volts from a two-byte value."""

    if len(value) != 2:
        raise ValueError("invalid voltage length")
    return int.from_bytes(value, "little") / 2048


def decode_temperature(value: bytes | bytearray) -> float:
    """Decode signed temperature in degrees Celsius."""

    if len(value) != 1:
        raise ValueError("invalid temperature length")
    return int.from_bytes(value, "little", signed=True) / 2


def decode_uptime(value: bytes | bytearray) -> int:
    """Decode uptime seconds."""

    if len(value) != 4:
        raise ValueError("invalid uptime length")
    return int.from_bytes(value, "little")


def decode_interval(value: bytes | bytearray) -> int:
    """Decode sample interval minutes."""

    if len(value) != 1 or value[0] == 0:
        raise ValueError("invalid sample interval")
    return value[0]


def decode_cursor(value: bytes | bytearray) -> int:
    """Decode and validate the circular history cursor."""

    if len(value) != 2:
        raise ValueError("invalid cursor length")
    cursor = int.from_bytes(value, "little")
    if cursor >= HISTORY_CAPACITY:
        raise ValueError("invalid history cursor")
    return cursor


def decode_history(value: bytes | bytearray) -> list[HistorySample]:
    """Decode packed three-byte history records."""

    if len(value) % 3:
        raise ValueError("invalid history payload length")
    return [
        HistorySample(
            decode_voltage(value[offset : offset + 2]),
            decode_temperature(value[offset + 2 : offset + 3]),
        )
        for offset in range(0, len(value), 3)
    ]


def history_request(start: int, end: int) -> bytes:
    """Build a request for half-open cursor range [start, end)."""

    if not 0 <= start <= end <= HISTORY_CAPACITY:
        raise ValueError("invalid history range")
    return b"\x03" + struct.pack("<HH", start, end - start)


def incremental_ranges(previous: int, current: int) -> list[tuple[int, int]]:
    """Return one or two chronological ranges, accounting for cursor wrap."""

    if previous == current:
        return []
    if previous < current:
        return [(previous, current)]
    ranges = [(previous, HISTORY_CAPACITY)]
    if current:
        ranges.append((0, current))
    return ranges


def initial_ranges(cursor: int, valid_records: int) -> list[tuple[int, int]]:
    """Return ranges for an initial full-history synchronization."""

    if valid_records >= HISTORY_CAPACITY:
        return [(cursor, HISTORY_CAPACITY), *([(0, cursor)] if cursor else [])]
    start = max(0, cursor - valid_records)
    return [(start, cursor)] if start != cursor else []


def chunk_ranges(
    ranges: list[tuple[int, int]], chunk_size: int
) -> list[tuple[int, int]]:
    """Split history ranges into bounded requests without changing their order."""

    if chunk_size <= 0:
        raise ValueError("invalid history chunk size")
    return [
        (chunk_start, min(chunk_start + chunk_size, end))
        for start, end in ranges
        for chunk_start in range(start, end, chunk_size)
    ]
