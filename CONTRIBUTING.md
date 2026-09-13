# Contributing

Contributions are welcome through issues and pull requests at
<https://github.com/Danw33/py-ctek-battery-sense>.

## Development setup

Use Python 3.11 or newer:

```console
python -m venv .venv
source .venv/bin/activate
python -m pip install --editable '.[dev]'
```

Before submitting a pull request, run:

```console
ruff check .
ruff format --check .
mypy
pytest
python -m build
twine check dist/*
```

Please add tests for behavioural changes and update the changelog for
user-visible changes. Do not include captures containing real Sender IDs,
Bluetooth addresses, or other personal data.

By contributing, you agree that your contribution is licensed under the
Apache License, Version 2.0.
