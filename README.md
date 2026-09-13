# Crestron DM NVX for Home Assistant

Local polling Home Assistant integration for Crestron DM NVX AV-over-IP
transmitters and receivers using the local CresNext REST API.

This project is a custom HACS integration for local control and monitoring of
DM NVX devices. It was built against live NVX hardware and the official DM NVX
REST API, with a few behavior notes captured from real devices where the API
docs are incomplete or optimistic.

Tested live against `DM-NVX-E30`, `DM-NVX-352`, `DM-NVX-350`, and
`DM-NVX-D30` hardware on firmware `7.1.5259.00090`.

## Installation With HACS

1. In Home Assistant, open HACS.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add this repository URL:

   ```text
   https://github.com/Phant0mElit3/ha-dm-nvx
   ```

4. Select category **Integration**.
5. Install **Crestron DM NVX**.
6. Restart Home Assistant.
7. Add the integration from **Settings > Devices & services**.

## Manual Installation

Copy `custom_components/crestron_nvx` into your Home Assistant config folder:

```text
/config/custom_components/crestron_nvx
```

Then restart Home Assistant.

## Configuration

The integration supports UI setup from **Settings > Devices & services**.

You will need:

- DM NVX host or IP address
- Web UI username
- Web UI password
- SSL verification setting
- Polling interval

Most DM NVX devices use a self-signed certificate, so SSL verification is
usually left disabled.

The integration detects whether each device is currently acting as a
transmitter or receiver during setup.

## Current Coverage

- Device metadata from `/Device/DeviceInfo`
- Device role from `/Device/DeviceSpecific/DeviceMode`
- Port counts from `/Device/DeviceCapabilities/PortConfig`
- Video resolution, HDCP state, HDMI sync/sink state, and Ethernet state
- Native binary sensors for HDMI signal/sink and network connectivity
- Receiver source selection through `/Device/AvRouting/Routes/0`
- Receiver audio breakaway routing through `AudioSource`
- Audio-follows-video toggle through `/Device/AvRouting/RouteControl`
- Receiver HDMI output enable/blanking
- Local HDMI input selection where supported
- Transmitter HDMI input selection where supported
- Transmitter test pattern selection where supported
- OSD notify entity and display-duration helper where OSD is supported
- Opt-in preview camera using the device JPEG preview endpoint
- CEC event listener for recognized source-device remote commands
- Identify switch for devices exposing the `Identify` object
- Redacted Home Assistant diagnostics for troubleshooting

## Entities

- `binary_sensor.<name>_signal_detected` or
  `binary_sensor.<name>_sink_connected`
- `binary_sensor.<name>_network_connected`
- `sensor.<name>_resolution`
- `sensor.<name>_hdcp_state`
- `sensor.<name>_network_status`
- `select.<name>_stream_source` on receivers
- `select.<name>_audio_source` on receivers when audio follows video is off
- `select.<name>_video_input` on receivers with a local HDMI input
- `select.<name>_hdmi_input` on multi-input transmitters
- `select.<name>_test_pattern` on supported transmitters
- `switch.<name>_identify` on supported devices
- `switch.<name>_audio_follows_video` on receivers
- `switch.<name>_hdmi_output` on receivers
- `camera.<name>_preview` when enabled in options and supported by the device
- `event.<name>_cec_command` on transmitters
- `notify.<name>_osd` and `number.<name>_osd_display_duration` where OSD is
  supported

## Known Notes

- The real API is HTTPS-only on port 443.
- Bare IP-address device cookies require an unsafe aiohttp cookie jar.
- Expired sessions may redirect to `/userlogin.html` instead of returning a
  clean `403`; the client disables redirects and reauthenticates on redirect
  status codes.
- The reliable receiver source-switching path is `AvRouting/Routes/0`, not
  direct writes to `StreamReceive`.
- Preview images are intentionally opt-in because they hit a heavier JPEG
  endpoint rather than the small JSON status objects.
- OSD, preview, test pattern, local input, and identify entities are created
  only when the device reports support.

## Documentation

- [Installation Guide](INSTALLATION.md)
- [API Notes](API_DOCUMENTATION.md)
- [Example Automations](configuration_example.yaml)
- [Project Structure](STRUCTURE.md)

## Crestron API

DM NVX REST API reference:

https://sdkcon78221.crestron.com/sdk/DM_NVX_REST_API/Content/Topics/API-Reference.htm

## Trademark Notice

This project is an independent Home Assistant custom integration and is not
affiliated with, endorsed by, sponsored by, or supported by Crestron
Electronics, Inc.

Crestron, DM NVX, and related names, marks, logos, and images are the property
of Crestron Electronics, Inc. The MIT license applies to the original
integration source code only and does not grant rights to Crestron trademarks,
logos, images, or other third-party assets.

See [NOTICE](NOTICE) for the trademark notice.
