# Changelog

## 2.2.0

- Fix recursive login attempts, handle HTTP 401, and serialize concurrent
  session renewal without repeating capability discovery.
- Surface failed polls as unavailable and invalid credentials as a Home
  Assistant reauthentication prompt. Close sessions on failed setup and unload.
- Add reauthentication, connection reconfiguration and polling-interval options.
  Preserve entity and device identifiers when the same device changes address.
- Distinguish duplicate source names, retain unknown current routes in selects,
  and add independent audio Off. Report rejected commands to service callers.
- Fix CEC failure backoff and automation examples, serialize OSD messages and
  clean up background tasks. Tie camera, CEC and OSD availability to device polling.
- Redact device identity and credentials in diagnostics; include polling status.
- Correct the Home Assistant minimum to 2024.12 for the options and notify APIs.
- Repair HACS and Hassfest metadata, bundle brand assets, add regression tests,
  CI compatibility checks, issue templates and versioned release packaging.

## 2.1.1

- Adds native Home Assistant binary sensors for HDMI signal/sink and network
  connectivity.
- Adds support for Crestron's `Identify` object as a switch on supported
  devices.
- Adds redacted Home Assistant diagnostics.
- Uses Home Assistant's aiohttp client session helper in runtime setup and
  config flow.
- Uses device serial number as the preferred config-entry unique ID.
- Treats partial Crestron POST failures as failures and logs result details.
- Cleans up CEC listener and OSD timer task cancellation on unload.
- Updates HACS/Home Assistant minimum version metadata to `2023.8.0`.

## 2.1.0

- Adds recovery from expired sessions that redirect to `/userlogin.html`.
- Adds retry-friendly setup behavior when a device is offline during Home
  Assistant startup.
- Adds real device metadata in Home Assistant device info.
- Adds optional preview camera support.
- Adds OSD notify and display-duration entities where supported.
- Adds CEC event listener support for recognized HDMI-CEC remote commands.
