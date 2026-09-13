# py-ctek-battery-sense: CTEK Battery Sense for Python

`ctek-battery-sense` is an **unofficial**, fully asynchronous, typed Python library for reading
CTEK Battery Sense Bluetooth Low Energy monitors. It supports live voltage and
temperature values, the sender's circular voltage history, and the State of
Charge calculation and battery-status thresholds similar to the CTEK app.

This is an independent, unofficial project. It is not affiliated with,
endorsed by, or supported by CTEK Sweden AB. CTEK and Battery Sense are
trademarks of their respective owner.

## Supported hardware

The library has been developed and tested with CTEK Battery Sense 40-149
(hardware platform CTEK1088). Other CTEK products are not currently supported.

## Installation

```console
python -m pip install ctek-battery-sense
```

## Usage

The monitor advertises as `CTEK` with service UUID
`812ca8ed-a177-4d74-aefa-70098c5416af`. A caller discovers the device with
Bleak, supplies the 11-character Sender ID printed on the unit, and retains the
`CTEKHistory` instance between reads so subsequent synchronizations are
incremental.

```python
from bleak import BleakScanner

from ctek_battery_sense import (
    CTEKBatterySense,
    CTEKHistory,
    SERVICE_UUID,
    battery_status,
    calculate_soc,
)


async def read_monitor(sender_id: str) -> None:
    device = await BleakScanner.find_device_by_filter(
        lambda _device, advertisement: SERVICE_UUID in advertisement.service_uuids
    )
    if device is None:
        raise RuntimeError("CTEK Battery Sense monitor not found")

    history = CTEKHistory()
    result = await CTEKBatterySense(device, sender_id).async_read(history)
    soc = calculate_soc(
        history.voltages or [result.voltage],
        capacity_ah=75,
        sample_minutes=result.sample_interval,
    )

    print(f"Voltage: {result.voltage:.2f} V")
    print(f"Temperature: {result.temperature:.1f} °C")
    print(f"State of charge: {soc:.0f}%")
    print(f"Battery status: {battery_status(soc)}")
```

The library performs Bluetooth I/O but does not schedule polling, persist
history, or select a Bluetooth adapter. Those responsibilities remain with the
calling application.

## Development

```console
python -m venv .venv
source .venv/bin/activate
python -m pip install --editable '.[dev]'
ruff check .
ruff format --check .
mypy
pytest
python -m build
twine check dist/*
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and
[CHANGELOG.md](CHANGELOG.md) for release notes.

## License

Copyright © 2026 Daniel Wilson ([@Danw33](https://github.com/Danw33))

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
