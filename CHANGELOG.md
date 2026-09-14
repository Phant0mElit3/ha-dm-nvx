# Changelog

## 2.3.0b1 (Prerelease)

- Add direct AES67 Stream selection on NVX decoders reporting one receive slot,
  including discovered compatible NAX media-player and physical-input feeds.
- Add Audio Output Mode for primary, automatic, or DM NAX (AES67) output audio.
- Require secondary Audio Follows Video to be explicitly disabled for independent
  receive changes. Preserve video routing, USB, NAX transmission, encryption,
  automatic initiation settings, and existing entity identifiers.
- Verify receive status and multicast endpoint after writes; never report a
  requested session alone as a successful switch. Serialize audio commands and
  refresh on failures. Discover optional controls on later polls as needed.
- Document the separate selectors, advertised names, setup, restoration,
  automation, and the fact that UID-route Off does not stop direct AES67 audio.
- Inspected the D30's live receive/discovery schema on firmware 7.1.5259.00090.
  New routing writes are regression-tested with mocked I/O, not yet confirmed
  audibly on hardware. Installation does not change playback. Stable 2.2.1
  remains the non-beta release.

## 2.2.1

- Reconcile video source changes with primary stream reception. When AvRouting
  accepts a source without updating reception, set its discovered RTSP URL on
  the primary StreamReceive slot and verify that the stream starts.
- Wait for stream processing to finish, serialize video changes, and bound
  source commands to 15 seconds once running. Surface failed confirmation to HA.
- Derive the video dropdown from receive location/status when supported, not
  just the requested route. Refresh after partially failed video commands.
- Preserve independent AES67 audio/USB routes and existing follow settings.
  Manual stream initiation is started explicitly without changing its mode.
  The existing full-route Off behavior is unchanged.
- Include receive status in diagnostics while redacting the stream URL.
- Reproduced an accepted route with no active reception on a DM-NVX-D30 running
  7.1.5259.00090; the user confirmed that setting the 363C RTSP location in the
  decoder web UI restores the picture. The updated integration is regression
  tested with mocked I/O; end-to-end HA installation testing remains necessary.

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
