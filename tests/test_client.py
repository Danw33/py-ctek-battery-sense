"""Tests for the CTEK Battery Sense BLE client."""

import asyncio
import struct
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from ctek_battery_sense.client import (
    CTEKBatterySense,
    CTEKPairingError,
)
from ctek_battery_sense.models import CTEKHistory
from ctek_battery_sense.protocol import (
    HISTORY_CURSOR_UUID,
    HISTORY_DATA_UUID,
    HISTORY_REQUEST_UUID,
    LIVE_TEMPERATURE_UUID,
    LIVE_VOLTAGE_UUID,
    SAMPLE_INTERVAL_UUID,
    UNLOCK_UUID,
    UPTIME_UUID,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
SENDER_ID = "Z99TEST0001"


def _device() -> BLEDevice:
    """Return a synthetic BLE device."""

    return BLEDevice(ADDRESS, "CTEK", {})


def _history_payload(count: int, voltage: float = 12.5) -> bytearray:
    """Return packed history records."""

    record = int(voltage * 2048).to_bytes(2, "little") + bytes([40])
    return bytearray(record * count)


def _connected_client(*, cursor: int = 2, uptime: int = 600) -> MagicMock:
    """Return a mock client which supplies readings and history notifications."""

    client = MagicMock()
    notification_callback = None

    async def start_notify(_uuid: str, callback) -> None:
        nonlocal notification_callback
        notification_callback = callback

    async def write_gatt_char(uuid: str, value: bytes, *, response: bool) -> None:
        if uuid != HISTORY_REQUEST_UUID:
            return
        assert response
        _, count = struct.unpack("<HH", value[1:])
        assert notification_callback is not None
        notification_callback(MagicMock(), _history_payload(count))

    values = {
        UPTIME_UUID: uptime.to_bytes(4, "little"),
        SAMPLE_INTERVAL_UUID: b"\x05",
        HISTORY_CURSOR_UUID: cursor.to_bytes(2, "little"),
        LIVE_VOLTAGE_UUID: int(12.5 * 2048).to_bytes(2, "little"),
        LIVE_TEMPERATURE_UUID: b"\x28",
    }
    client.read_gatt_char = AsyncMock(side_effect=lambda uuid: values[uuid])
    client.write_gatt_char = AsyncMock(side_effect=write_gatt_char)
    client.start_notify = AsyncMock(side_effect=start_notify)
    client.stop_notify = AsyncMock()
    client.pair = AsyncMock()
    client.disconnect = AsyncMock()
    return client


async def test_read_initial_history() -> None:
    """Test live readings and the initial bounded history download."""

    connection = _connected_client()
    history = CTEKHistory()
    with (
        patch(
            "ctek_battery_sense.client.establish_connection",
            new=AsyncMock(return_value=connection),
        ),
        patch(
            "ctek_battery_sense.client.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await CTEKBatterySense(_device(), SENDER_ID).async_read(history)

    assert result.voltage == 12.5
    assert result.temperature == 20
    assert result.uptime == 600
    assert result.sample_interval == 5
    assert result.cursor == 2
    assert history == CTEKHistory(cursor=2, sample_interval=5, voltages=[12.5, 12.5])
    connection.pair.assert_awaited_once()
    connection.start_notify.assert_awaited_once_with(HISTORY_DATA_UUID, ANY)
    connection.stop_notify.assert_awaited_once_with(HISTORY_DATA_UUID)
    connection.disconnect.assert_awaited_once()


async def test_read_incremental_history() -> None:
    """Test downloading only records added after the cached cursor."""

    connection = _connected_client(cursor=4)
    history = CTEKHistory(cursor=2, sample_interval=5, voltages=[12.4, 12.45])
    with (
        patch(
            "ctek_battery_sense.client.establish_connection",
            new=AsyncMock(return_value=connection),
        ),
        patch(
            "ctek_battery_sense.client.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        await CTEKBatterySense(_device(), SENDER_ID).async_read(history)

    assert history.cursor == 4
    assert history.voltages == [12.4, 12.45, 12.5, 12.5]


async def test_read_without_new_history() -> None:
    """Test an unchanged cursor does not subscribe for history data."""

    connection = _connected_client(cursor=2)
    history = CTEKHistory(cursor=2, sample_interval=5, voltages=[12.5])
    with (
        patch(
            "ctek_battery_sense.client.establish_connection",
            new=AsyncMock(return_value=connection),
        ),
        patch(
            "ctek_battery_sense.client.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        await CTEKBatterySense(_device(), SENDER_ID).async_read(history)

    connection.start_notify.assert_not_awaited()
    assert history.voltages == [12.5]


async def test_validate() -> None:
    """Test validating credentials reads a protected value and disconnects."""

    connection = _connected_client()
    with (
        patch(
            "ctek_battery_sense.client.establish_connection",
            new=AsyncMock(return_value=connection),
        ),
        patch(
            "ctek_battery_sense.client.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        await CTEKBatterySense(_device(), SENDER_ID).async_validate()

    connection.read_gatt_char.assert_awaited_once_with(LIVE_VOLTAGE_UUID)
    connection.disconnect.assert_awaited_once()


@pytest.mark.parametrize("failure_stage", ["unlock", "pair"])
async def test_pairing_errors(failure_stage: str) -> None:
    """Test unlock and link-security failures become pairing errors."""

    connection = _connected_client()
    if failure_stage == "unlock":
        connection.write_gatt_char = AsyncMock(side_effect=BleakError("write failed"))
    else:
        connection.pair = AsyncMock(side_effect=TimeoutError)

    with (
        patch(
            "ctek_battery_sense.client.establish_connection",
            new=AsyncMock(return_value=connection),
        ),
        patch(
            "ctek_battery_sense.client.asyncio.sleep",
            new=AsyncMock(),
        ),
        pytest.raises(CTEKPairingError),
    ):
        await CTEKBatterySense(_device(), SENDER_ID).async_validate()

    connection.disconnect.assert_awaited_once()


async def test_history_download_timeout() -> None:
    """Test a missing history notification times out and unsubscribes."""

    connection = MagicMock(
        start_notify=AsyncMock(),
        write_gatt_char=AsyncMock(),
        stop_notify=AsyncMock(),
    )
    client = CTEKBatterySense(_device(), SENDER_ID)
    timeout = asyncio.timeout
    with (
        patch(
            "ctek_battery_sense.client.asyncio.timeout",
            side_effect=lambda _seconds: timeout(0),
        ),
        pytest.raises(BleakError, match="Timed out downloading"),
    ):
        await client._async_download(connection, [(0, 1)])

    connection.stop_notify.assert_awaited_once_with(HISTORY_DATA_UUID)


async def test_unlock_value_is_written_with_response() -> None:
    """Test the application unlock write uses a response."""

    connection = _connected_client()
    with patch("ctek_battery_sense.client.asyncio.sleep", new=AsyncMock()):
        await CTEKBatterySense(_device(), SENDER_ID)._async_unlock_and_pair(connection)

    unlock_call = connection.write_gatt_char.await_args_list[0]
    assert unlock_call.args[0] == UNLOCK_UUID
    assert len(unlock_call.args[1]) == 16
    assert unlock_call.kwargs == {"response": True}
