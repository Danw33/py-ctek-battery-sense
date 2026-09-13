"""Tests for the CTEK protocol codecs."""

from collections.abc import Callable

import pytest

from ctek_battery_sense.protocol import (
    chunk_ranges,
    decode_cursor,
    decode_history,
    decode_interval,
    decode_temperature,
    decode_uptime,
    decode_voltage,
    full_barcode,
    history_request,
    incremental_ranges,
    initial_ranges,
    normalize_sender_id,
    unlock_value,
)


def test_sender_id_normalization_and_unlock() -> None:
    """Sender IDs and full barcodes produce the same deterministic unlock."""

    synthetic_sender_id = "Z99TEST0001"
    synthetic_barcode = f"40149{synthetic_sender_id}"

    assert full_barcode(f" {synthetic_sender_id.lower()} ") == synthetic_barcode
    assert normalize_sender_id(synthetic_barcode) == synthetic_sender_id
    assert unlock_value(synthetic_sender_id).hex() == "6e477c9908dc57824d03a98c8b8b1dfa"
    assert unlock_value(synthetic_barcode) == unlock_value(synthetic_sender_id)


@pytest.mark.parametrize("value", ["", "SHORT", "40149INVALID-ID"])
def test_invalid_sender_id(value: str) -> None:
    """Malformed Sender IDs are rejected."""

    with pytest.raises(ValueError, match="invalid_sender_id"):
        full_barcode(value)


def test_live_decoding() -> None:
    """Live values must match the observed app display."""

    assert decode_voltage(bytes.fromhex("b162")) == 12.33642578125
    assert decode_temperature(bytes.fromhex("37")) == 27.5
    assert decode_temperature(bytes.fromhex("fe")) == -1
    assert decode_uptime(bytes.fromhex("78563412")) == 0x12345678
    assert decode_interval(bytes.fromhex("78")) == 120
    assert decode_cursor(bytes.fromhex("1027")) == 10_000


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        (decode_voltage, b"\x00"),
        (decode_temperature, b""),
        (decode_uptime, b"\x00"),
        (decode_interval, b"\x00"),
        (decode_cursor, b"\x30\x75"),
        (decode_history, b"\x00"),
    ],
)
def test_invalid_payloads(decoder: Callable[[bytes], object], payload: bytes) -> None:
    """Malformed GATT payloads are rejected."""

    with pytest.raises(ValueError):
        decoder(payload)


def test_history_and_requests() -> None:
    """History records and request ranges must match the captures."""

    assert len(decode_history(bytes.fromhex("196571db6472"))) == 2
    assert history_request(8493, 20053).hex() == "032d21282d"
    assert incremental_ranges(29990, 10) == [(29990, 30000), (0, 10)]
    assert incremental_ranges(10, 10) == []
    assert initial_ranges(20053, 30000) == [(20053, 30000), (0, 20053)]


def test_history_ranges_are_bounded_for_proxy_backpressure() -> None:
    """Large and wrapped downloads must preserve order in small requests."""

    assert chunk_ranges([(29950, 30000), (0, 75)], 60) == [
        (29950, 30000),
        (0, 60),
        (60, 75),
    ]
