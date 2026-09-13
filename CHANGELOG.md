# Changelog

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

