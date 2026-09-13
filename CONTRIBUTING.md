# Contributing

Open an issue with the device model, firmware, transmitter/receiver role, and
the behavior you expect. For API changes, include the official Crestron object
reference and a redacted sample response when available.

## Local Checks

Use Python 3.12 for the minimum supported Home Assistant version:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install homeassistant==2024.12.5 -r requirements-test.txt
ruff check custom_components tests scripts
ruff format --check custom_components tests scripts
python -m pytest -q
python scripts/build_release.py
```

CI also checks the latest Home Assistant release on Python 3.14. Tests use real
Home Assistant classes with mocked device I/O; they do not contact NVX hardware.

## Device Testing

`test_live.py` is a separate interactive hardware harness. Create a local `.env`
using `.env.example`; do not commit credentials or raw device captures. Run
`python test_live.py` for read-only checks. Its `--write`, `--osd`,
`--testpattern`, `--output`, and `--reconnect` options change device state or
sessions and should only be used on a device you can interrupt.

Keep recorded hardware observations separate from documentation-based claims.
Gate optional entities on device support, preserve existing entity identifiers,
and reconcile accepted commands with device reads. Add regression tests for
protocol and lifecycle changes.

## Releases

Update `VERSION`, the integration manifest and `CHANGELOG.md` together. Run
validation, tag the passing commit with `v<version>`, then run the Release
workflow for that tag. It verifies the version and attaches a manual-install
ZIP. HACS installs the integration directory from the release tag.
