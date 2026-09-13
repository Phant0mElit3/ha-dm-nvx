{% if installed %}
## Changes in {{version}}

Check the [release notes](https://github.com/Phant0mElit3/ha-dm-nvx/releases) for details.

{% endif %}

## Crestron DM NVX for Home Assistant

Control Crestron DM NVX AV-over-IP transmitters and receivers directly from Home Assistant.

### Features

- 📺 **Real-time Status Monitoring** - Resolution, HDMI signal/sink status, HDCP state, network status, and native connectivity binary sensors
- 🎛️ **Stream Switching** - Dropdown to switch a receiver's video, audio, and USB together to any discovered source
- 🎮 **CEC Listener** - fires Home Assistant events (power/volume/mute) when a connected source device (e.g. an Apple TV) sends CEC remote commands, for use as automation triggers
- 🔄 **Automatic Discovery** - Receivers automatically discover available transmitter streams
- 🎨 **Test Patterns** - put up SMPTE color bars, black/white fields, or gradients on a transmitter for verifying the signal chain without a real source
- 🖥️ **HDMI Output Control** - force-blank a receiver's output independent of routing
- 💡 **Identify Mode** - flash supported devices' LEDs to locate the physical unit
- 📷 **Preview Camera** (opt-in) - live JPEG snapshot of what a device currently shows
- ⚡ **Configurable polling interval** (10-300 seconds) for status; CEC events are pushed in near-real-time via long-poll, not on the polling interval

### Supported Devices

Verified against DM-NVX-E30, DM-NVX-352, DM-NVX-350, and DM-NVX-D30 (firmware
7.1.5259.00090). Other DM NVX models with REST API support should work but
haven't been directly tested.

### Quick Start

1. Install via HACS
2. Go to Settings → Devices & Services
3. Click Add Integration
4. Search for "Crestron DM NVX"
5. Enter the device's IP, username/password (authentication must already be
   enabled on the device - the API is HTTPS-only)
6. Role (Transmitter/Receiver) is detected automatically

### Requirements

- Home Assistant 2023.8.0 or newer
- Crestron DM NVX devices on your network, reachable over HTTPS (port 443),
  with authentication enabled
- Valid admin credentials for each device

### Documentation

- [Installation Guide](https://github.com/Phant0mElit3/ha-dm-nvx/blob/main/INSTALLATION.md)
- [API Documentation](https://github.com/Phant0mElit3/ha-dm-nvx/blob/main/API_DOCUMENTATION.md)
- [Configuration Examples](https://github.com/Phant0mElit3/ha-dm-nvx/blob/main/configuration_example.yaml)

### Support

Found a bug or have a feature request? [Open an issue](https://github.com/Phant0mElit3/ha-dm-nvx/issues)!
