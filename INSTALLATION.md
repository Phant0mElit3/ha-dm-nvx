# Installation and Troubleshooting

## Requirements

- Home Assistant 2024.12 or newer.
- An NVX transmitter/receiver with authentication enabled.
- Device admin credentials and HTTPS connectivity on port 443.
- HACS for HACS installation, or access to the Home Assistant configuration directory.

The 2023.8 minimum advertised in earlier versions was incorrect for the
notify entity and options-flow APIs already used by this integration.

## HACS

1. [Install HACS](https://www.hacs.xyz/docs/use/download/download/) if needed.
2. In HACS, open the menu and select **Custom repositories**.
3. Add `https://github.com/Phant0mElit3/ha-dm-nvx`, type **Integration**.
4. Download **Crestron DM NVX** and restart Home Assistant.
5. Go to **Settings > Devices & services > Add integration** and search for
   **Crestron DM NVX**.

[Open this repository in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=Phant0mElit3&repository=ha-dm-nvx&category=integration).
This is a custom repository, not a default-catalog listing.

## Manual Install

Download the attached `crestron_nvx-<version>.zip` from the
[latest release](https://github.com/Phant0mElit3/ha-dm-nvx/releases/latest).
Extract it into the Home Assistant configuration directory. The resulting path
must be `/config/custom_components/crestron_nvx/manifest.json` on Home Assistant OS.

Alternatively, copy the entire `custom_components/crestron_nvx/` directory
from the repository, including `translations/` and `brand/`.
Restart Home Assistant, then add the integration.

## Device Setup

Enter a friendly name, IP address or hostname, username, password, certificate
verification setting and polling interval. Enter just the address, such as
`192.0.2.10` or `nvx.local`, without a URL scheme, port or path.

Leave certificate verification off for factory self-signed certificates.
Enable it when the device has a certificate trusted by Home Assistant.
The connection remains HTTPS even when certificate verification is off.

Configure one entry per device. Role and optional capabilities are detected.
There is no YAML device configuration.

## Options and Reconfiguration

**Configure/Options** controls the polling interval (10-300 seconds) and the
preview camera. Saving options reloads the integration.

The entry menu's **Reconfigure** action changes the device address or
credentials. Re-enter the password to validate the connection. The device
serial must match; a different device requires a separate entry. Existing
entity and device IDs are retained, including IDs from earlier versions.

When saved credentials stop working, Home Assistant prompts for
reauthentication. An offline device retries automatically.

## Verify

Open the device under **Settings > Devices & services > Crestron DM NVX**.
Entity IDs are based on the name you supplied, not necessarily on
`crestron_nvx`; use the device's entity list to find the actual IDs.

- Confirm HDMI connection, resolution and network state.
- On a receiver, choose a video stream and verify the display changes.
- Disable Audio Follows Video to use the independent Audio Source select.
- Enable preview in Options to create a camera on supported devices.
- Use `notify.send_message` with the OSD entity on supported models.
- For CEC, inspect the event entity's timestamp and `event_type` attribute.
  Use a state trigger as shown in [the examples](configuration_example.yaml).

Legacy text sensors for HDMI/network connectivity remain for existing
automations. Prefer the native binary sensors for new automations.

## Troubleshooting

### Integration cannot be found

Check the component directory, restart Home Assistant and inspect
**Settings > System > Logs**. Ensure the minimum Home Assistant version is met.

### Cannot connect

Open `https://<device-address>/userlogin.html` and verify the device web UI
loads. Check port 443, network routing and device authentication. A trusted
certificate is required only when certificate verification is enabled.

### Invalid authentication

Verify the same credentials work in the device web UI. Reconfigure the
integration or complete the reauthentication prompt after changing credentials.
The client handles expired sessions, including login redirects and HTTP 401/403.

### No stream sources

Confirm the transmitter is powered and transmitting. Check NVX multicast,
IGMP and discovery configuration across the relevant network segments.
An existing route may appear as `Unknown (<ID>)` until discovery catches up.
Duplicate names receive an ID suffix.

### No CEC commands

The connected source must actually send CEC commands on HDMI input 1.
For Apple TV, select HDMI-CEC volume control. IR/Bluetooth-only remote commands
will not reach this listener. Supported events are `power_on`, `power_off`,
`volume_up`, `volume_down` and `mute`; this integration does not send CEC.

### Preview unavailable or blank

Enable preview in Options. The device must expose its Preview object and
return a JPEG; capabilities vary by model, firmware and content protection.
The camera becomes unavailable while device polling is failing.

### Switch state changes after a delay

The integration reads actual device state. Some firmware accepts a command
before applying it. Allow the next polling cycle to reconcile the change.

## Diagnostics and Logs

Use the entry menu to download diagnostics. Credentials, configured
names/addresses and serial numbers are redacted. Review the file before sharing.
Enable debug logging from the integration entry while reproducing an issue,
then disable it and include only relevant log excerpts.

Report bugs through [GitHub issues](https://github.com/Phant0mElit3/ha-dm-nvx/issues/new/choose).

## Update or Remove

For HACS updates, download the release and restart Home Assistant. For manual
updates, replace the integration directory with the matching release contents
and restart. Review the changelog first.

To remove it, delete each integration entry, remove the repository in HACS
(or the component directory for a manual install), then restart.
