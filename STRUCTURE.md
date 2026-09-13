# Repository Structure

| Path | Purpose |
| --- | --- |
| `custom_components/crestron_nvx/` | Complete installable integration |
| `__init__.py` inside the integration | Entry lifecycle and polling coordinator |
| `crestron_nvx_api.py` | Authentication, REST requests, routing, OSD and CEC decoding |
| `config_flow.py` | Setup, reauthentication, reconfiguration and options |
| `entity.py` | Stable device identity and command-result handling |
| `sensor.py`, `binary_sensor.py` | Video and primary-network status |
| `select.py`, `switch.py` | Routing, input selection, test patterns, output and Identify |
| `event.py` | Passive CEC listener with cancellation and failure backoff |
| `notify.py`, `number.py` | OSD messages, auto-clear and restored duration |
| `camera.py` | Opt-in JPEG snapshots |
| `diagnostics.py` | Redacted connection/capability diagnostics |
| `strings.json`, `translations/en.json` | Setup and options translations |
| `brand/` inside the integration | Bundled integration icons |
| `tests/` | Automated API and Home Assistant regression tests |
| `test_live.py` | Interactive hardware harness; separate from pytest |
| `scripts/build_release.py` | Version-checked manual-install ZIP |
| `.github/workflows/` | Tests, HACS/Hassfest validation and releases |
| `.github/ISSUE_TEMPLATE/` | Bug and feature request forms |
| `.env.example` | Placeholder hardware-test configuration |
| `README.md`, `INSTALLATION.md` | User documentation |
| `API_DOCUMENTATION.md` | Existing hardware observations and API notes |
| `CONTRIBUTING.md` | Developer checks and release instructions |
| `CHANGELOG.md`, `VERSION` | Release history and version |
| `hacs.json` | HACS installation metadata |
| `LICENSE`, `NOTICE` | License and trademark attribution |

See [CONTRIBUTING.md](CONTRIBUTING.md) for local testing. Generated archives
live in `dist/` and are not committed. Real `.env` credentials and Python
test caches are also ignored.
