"""Async BLE client for CTEK Battery Sense."""

import asyncio
import logging
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .models import CTEKHistory, CTEKReadResult
from .protocol import (
    HISTORY_CAPACITY,
    HISTORY_CURSOR_UUID,
    HISTORY_DATA_UUID,
    HISTORY_REQUEST_UUID,
    LIVE_TEMPERATURE_UUID,
    LIVE_VOLTAGE_UUID,
    SAMPLE_INTERVAL_UUID,
    UNLOCK_UUID,
    UPTIME_UUID,
    chunk_ranges,
    decode_cursor,
    decode_history,
    decode_interval,
    decode_temperature,
    decode_uptime,
    decode_voltage,
    history_request,
    incremental_ranges,
    initial_ranges,
    unlock_value,
)

_LOGGER = logging.getLogger(__name__)

# The legacy sender needs a short delay after connection and service discovery.
CONNECTION_SETTLE_SECONDS = 1.0
# The sender also needs time to process the unlock before link security starts.
UNLOCK_SETTLE_SECONDS = 2.0
# The monitor sends six history records per notification at MTU 23. Limiting a
# request to 60 records bounds each burst to ten proxy messages and makes the
# next request dependent on receipt of the previous burst in Home Assistant.
HISTORY_DOWNLOAD_CHUNK_RECORDS = 60


class CTEKPairingError(BleakError):
    """Raised when the encrypted CTEK link cannot be established."""


class CTEKBatterySense:
    """Connect to and read a CTEK monitor."""

    def __init__(self, device: BLEDevice, sender_id: str) -> None:
        """Initialize the client."""

        self._device = device
        self._sender_id = sender_id

    async def async_read(self, history: CTEKHistory) -> CTEKReadResult:
        """Read live values and synchronize voltage history."""

        client = await establish_connection(
            BleakClientWithServiceCache,
            self._device,
            f"CTEK {self._sender_id}",
            max_attempts=3,
        )
        try:
            await self._async_unlock_and_pair(client)
            uptime = decode_uptime(await client.read_gatt_char(UPTIME_UUID))
            interval = decode_interval(
                await client.read_gatt_char(SAMPLE_INTERVAL_UUID)
            )
            cursor = decode_cursor(await client.read_gatt_char(HISTORY_CURSOR_UUID))
            voltage = decode_voltage(await client.read_gatt_char(LIVE_VOLTAGE_UUID))
            temperature = decode_temperature(
                await client.read_gatt_char(LIVE_TEMPERATURE_UUID)
            )

            if history.cursor is None or history.sample_interval != interval:
                history.voltages.clear()
                valid_records = min(HISTORY_CAPACITY, uptime // (interval * 60))
                ranges = initial_ranges(cursor, valid_records)
            else:
                ranges = incremental_ranges(history.cursor, cursor)

            history.voltages.extend(await self._async_download(client, ranges))
            history.voltages = history.voltages[-HISTORY_CAPACITY:]
            history.cursor = cursor
            history.sample_interval = interval
            return CTEKReadResult(
                voltage=voltage,
                temperature=temperature,
                uptime=uptime,
                sample_interval=interval,
                cursor=cursor,
            )
        finally:
            await client.disconnect()

    async def async_validate(self) -> None:
        """Validate the Sender ID by authenticating and reading one value."""

        client = await establish_connection(
            BleakClientWithServiceCache,
            self._device,
            f"CTEK {self._sender_id}",
            max_attempts=2,
        )
        try:
            await self._async_unlock_and_pair(client)
            decode_voltage(await client.read_gatt_char(LIVE_VOLTAGE_UUID))
        finally:
            await client.disconnect()

    async def _async_unlock_and_pair(self, client: BleakClient) -> None:
        """Application-unlock the monitor, then establish the encrypted bond."""

        _LOGGER.debug(
            "Waiting %.1f seconds before unlocking CTEK %s",
            CONNECTION_SETTLE_SECONDS,
            self._device.address,
        )
        await asyncio.sleep(CONNECTION_SETTLE_SECONDS)
        _LOGGER.debug(
            "Writing application unlock characteristic on CTEK %s",
            self._device.address,
        )
        try:
            await client.write_gatt_char(
                UNLOCK_UUID, unlock_value(self._sender_id), response=True
            )
        except (BleakError, TimeoutError) as err:
            raise CTEKPairingError(
                f"Unable to write the CTEK unlock value "
                f"with {self._device.address}: {err}"
            ) from err
        _LOGGER.debug(
            "CTEK unlock succeeded for %s; waiting %.1f seconds before pairing",
            self._device.address,
            UNLOCK_SETTLE_SECONDS,
        )
        await asyncio.sleep(UNLOCK_SETTLE_SECONDS)
        _LOGGER.debug("Requesting BLE pairing with CTEK %s", self._device.address)
        try:
            await client.pair()
        except (BleakError, TimeoutError) as err:
            raise CTEKPairingError(
                f"Unable to pair with unlocked CTEK {self._device.address}: {err}"
            ) from err
        _LOGGER.debug("BLE pairing succeeded for CTEK %s", self._device.address)

    async def _async_download(
        self, client: BleakClient, ranges: list[tuple[int, int]]
    ) -> list[float]:
        """Download requested history ranges in chronological order."""

        voltages: list[float] = []
        received = 0
        event = asyncio.Event()

        def notification(_characteristic: Any, payload: bytearray) -> None:
            nonlocal received
            samples = decode_history(payload)
            voltages.extend(sample.voltage for sample in samples)
            received += len(samples)
            event.set()

        if not ranges:
            return voltages
        chunks = chunk_ranges(ranges, HISTORY_DOWNLOAD_CHUNK_RECORDS)
        expected = sum(end - start for start, end in chunks)
        _LOGGER.debug(
            "Downloading %d CTEK history records in %d bounded requests",
            expected,
            len(chunks),
        )
        await client.start_notify(HISTORY_DATA_UUID, notification)
        try:
            for start, end in chunks:
                target = received + end - start
                await client.write_gatt_char(
                    HISTORY_REQUEST_UUID,
                    history_request(start, end),
                    response=True,
                )
                while received < target:
                    event.clear()
                    if received >= target:
                        break
                    try:
                        async with asyncio.timeout(20):
                            await event.wait()
                    except TimeoutError as err:
                        raise BleakError(
                            "Timed out downloading CTEK history "
                            f"({received}/{expected} records received)"
                        ) from err
        finally:
            await client.stop_notify(HISTORY_DATA_UUID)
        _LOGGER.debug("Downloaded %d CTEK history records", received)
        return voltages
