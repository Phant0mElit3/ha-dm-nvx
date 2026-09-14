"""Client for the real Crestron DM NVX REST API (CresNext).

Reference: https://sdkcon78221.crestron.com/sdk/DM_NVX_REST_API/
Existing hardware observations cover DM-NVX-E30, DM-NVX-352, DM-NVX-350
and DM-NVX-D30. Maintenance fixes are regression-tested with mocked I/O;
capabilities such as Identify are probed rather than assumed.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional
from urllib.parse import urlsplit

import aiohttp

from .aes67 import (
    AUDIO_OUTPUT_MODES,
    compatible_stream,
    single_receiver,
    stream_endpoint,
    stream_started,
    valid_key,
)

_LOGGER = logging.getLogger(__name__)

LOGIN_PATH = "/userlogin.html"
LOGOUT_PATH = "/logout"
LONGPOLL_PATH = "/Device/Longpoll"

DEVICE_MODE_TRANSMITTER = "Transmitter"
DEVICE_MODE_RECEIVER = "Receiver"
VIDEO_ROUTE_TIMEOUT = 15
AUDIO_ROUTE_TIMEOUT = 15

# CEC opcodes/operands relevant to the "listen for Apple TV remote presses"
# use case. Confirmed live: a real Volume Up press decoded to bytes
# 40 44 41 -> header 0x40 (source 4 "Playback Device 1" = the Apple TV,
# dest 0 "TV") + opcode 0x44 (User Control Pressed) + operand 0x41 (Volume Up).
_CEC_OPCODE_IMAGE_VIEW_ON = 0x04
_CEC_OPCODE_STANDBY = 0x36
_CEC_OPCODE_USER_CONTROL_PRESSED = 0x44
_CEC_OPCODE_USER_CONTROL_RELEASED = 0x45  # key-up; intentionally ignored

_CEC_STANDALONE_EVENTS = {
    _CEC_OPCODE_IMAGE_VIEW_ON: "power_on",
    _CEC_OPCODE_STANDBY: "power_off",
}
_CEC_USER_CONTROL_EVENTS = {
    0x41: "volume_up",
    0x42: "volume_down",
    0x43: "mute",
}

# A stale/expired session doesn't get a clean 403 like the docs say - confirmed
# live by logging out server-side and reusing the old cookies: the device
# returns "301 Moved Permanently" / "Location: /userlogin.html" instead. If
# that redirect is auto-followed (aiohttp's default), the response looks like
# a normal 200 containing the login page's HTML rather than JSON, the session
# never gets renewed, and every request fails the same way forever - this is
# why the integration previously wouldn't recover after a disconnect.
# Redirects must be disabled per-request (see _request) and treated the same
# as 403 here.
_REAUTH_STATUSES = frozenset({301, 302, 303, 307, 308, 401, 403})


class CrestronNVXError(Exception):
    """Base error for this client."""


class CrestronNVXAuthError(CrestronNVXError):
    """Login failed (bad credentials or device rejected the session)."""


class CrestronNVXConnectionError(CrestronNVXError):
    """Device could not be reached."""


def decode_cec_message(b64_message: Optional[str]) -> Optional[str]:
    """Decode a raw base64 CEC frame into an HA event type, or None.

    The frame is base64(header_byte + opcode [+ operand]) with no other
    wrapping - confirmed by decoding real captured ReceiveCecMessage values.
    """
    if not b64_message:
        return None
    try:
        frame = base64.b64decode(b64_message, validate=True)
    except (ValueError, TypeError):
        return None
    if len(frame) < 2:
        return None
    opcode = frame[1]
    if opcode in _CEC_STANDALONE_EVENTS:
        return _CEC_STANDALONE_EVENTS[opcode]
    if opcode == _CEC_OPCODE_USER_CONTROL_PRESSED and len(frame) >= 3:
        return _CEC_USER_CONTROL_EVENTS.get(frame[2])
    return None


class CrestronNVXDevice:
    """A single Crestron NVX transmitter or receiver."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        verify_ssl: bool = False,
        name: Optional[str] = None,
    ) -> None:
        self.host = host
        self.entity_id_prefix = host
        self.name = name or host
        self.username = username
        self.password = password
        self.device_mode: Optional[str] = None
        self.hdmi_inputs = 0
        self.hdmi_outputs = 0
        self.osd_supported = False
        self.test_patterns: list[str] = []
        self.preview_supported = False
        self.identify_supported = False
        self.model: Optional[str] = None
        self.serial_number: Optional[str] = None
        self.firmware_version: Optional[str] = None
        # Not a device-side setting - purely how long this integration
        # leaves OSD text on screen before auto-clearing it. Mutated
        # directly by number.py's OSD Display Duration entity.
        self.osd_display_seconds = 5
        self._session = session
        self._ssl = None if verify_ssl else False
        self._base_url = f"https://{host}"
        self._authenticated = False
        self._auth_lock = asyncio.Lock()
        self._auth_generation = 0
        self._video_route_lock = asyncio.Lock()
        self._audio_route_lock = asyncio.Lock()

    @property
    def is_receiver(self) -> bool:
        return self.device_mode == DEVICE_MODE_RECEIVER

    @property
    def is_transmitter(self) -> bool:
        return self.device_mode == DEVICE_MODE_TRANSMITTER

    async def login(self) -> None:
        """Authenticate, then discover capabilities once during setup."""
        await self._authenticate()
        await self._load_port_config()
        self.osd_supported = (await self.get_osd()) is not None
        await self._load_test_patterns()
        await self._load_preview_supported()
        await self._load_identify_supported()
        await self._load_device_info()

    async def _authenticate(self, generation: int | None = None) -> None:
        """Serialize renewal so polling and camera requests share one login."""
        async with self._auth_lock:
            if generation is not None and generation != self._auth_generation:
                return
            self._authenticated = False
            await self._login_session()
            # A 200 login page is not proof of authentication. Probe once,
            # without renewal, to prevent recursive logins with bad credentials.
            data = await self._request("DeviceSpecific/DeviceMode", _retry=False)
            mode = ((data or {}).get("Device") or {}).get("DeviceSpecific", {}).get("DeviceMode")
            if mode not in (DEVICE_MODE_RECEIVER, DEVICE_MODE_TRANSMITTER):
                raise CrestronNVXConnectionError("Device did not return a valid NVX device mode")
            self.device_mode = mode
            self._authenticated = True
            self._auth_generation += 1

    async def _login_session(self) -> None:
        """Authenticate and establish the cookie session.

        DM NVX auth: GET /userlogin.html to seed the TRACKID cookie, then
        POST credentials to the same path. On success the device sets 6
        cookies (AuthByPasswd, TRACKID, iv, tag, userid, userstr) which
        aiohttp's cookie jar stores/resends automatically from here on.
        """
        try:
            async with self._session.get(
                f"{self._base_url}{LOGIN_PATH}",
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise CrestronNVXConnectionError(f"Login page returned HTTP {response.status}")
                await response.read()
            async with self._session.post(
                f"{self._base_url}{LOGIN_PATH}",
                data={"login": self.username, "passwd": self.password},
                headers={
                    "Origin": self._base_url,
                    "Referer": f"{self._base_url}{LOGIN_PATH}",
                },
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise CrestronNVXAuthError(
                        f"Login to {self.host} failed with HTTP {response.status}"
                    )
                if response.status not in (200, 302, 303):
                    raise CrestronNVXConnectionError(f"Login returned HTTP {response.status}")
                await response.read()
        except TimeoutError as err:
            raise CrestronNVXConnectionError(f"Timeout connecting to {self.host}") from err
        except aiohttp.ClientError as err:
            raise CrestronNVXConnectionError(f"Error connecting to {self.host}: {err}") from err

    async def _load_device_info(self) -> None:
        """Cache Model/SerialNumber/DeviceVersion - static for the device's lifetime."""
        info = await self.get_device_info()
        if not info:
            return
        self.model = info.get("Model")
        self.serial_number = info.get("SerialNumber")
        self.firmware_version = info.get("DeviceVersion")

    async def _load_port_config(self) -> None:
        """Cache HDMI input/output counts - static for the device's lifetime.

        Port count varies a lot across models even within one role (e.g. a
        DM-NVX-352 transmitter has 2 HDMI inputs, a DM-NVX-E30 has 1), so
        this is read once here rather than assumed, and used by entity
        setup to decide whether input-switching entities make sense at all.
        """
        data = await self._request("DeviceCapabilities/PortConfig")
        try:
            port_config = data["Device"]["DeviceCapabilities"]["PortConfig"]
        except (KeyError, TypeError):
            return
        if not isinstance(port_config, dict):
            return
        self.hdmi_inputs = port_config.get("NumberOfHdmiInputs", 0)
        self.hdmi_outputs = port_config.get("NumberOfHdmiOutputs", 0)

    async def _load_test_patterns(self) -> None:
        """Cache supported test pattern names - a transmitter-only feature.

        Receivers return an empty {"Device": {}} for this path - confirmed
        live - so an empty list here just means the device doesn't have it,
        same as the missing-object convention used elsewhere in this API.
        """
        data = await self._request("TestPatternConfig")
        try:
            self.test_patterns = data["Device"]["TestPatternConfig"]["TestPatternsSupported"]
        except (KeyError, TypeError):
            self.test_patterns = []
        if not isinstance(self.test_patterns, list):
            self.test_patterns = []
        self.test_patterns = [value for value in self.test_patterns if isinstance(value, str)]

    async def _load_preview_supported(self) -> None:
        """Cache whether this device exposes the /preview JPEG snapshot feature."""
        data = await self._request("Preview")
        try:
            preview = data["Device"]["Preview"]
        except (KeyError, TypeError):
            preview = None
        self.preview_supported = isinstance(preview, dict) and bool(preview)

    async def _load_identify_supported(self) -> None:
        """Cache whether this device exposes the Identify object."""
        self.identify_supported = (await self.get_identify()) is not None

    async def logout(self) -> None:
        """End the session. Best-effort; errors are not fatal."""
        if not self._authenticated:
            return
        try:
            async with self._session.get(
                f"{self._base_url}{LOGOUT_PATH}",
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                await response.read()
        except (TimeoutError, aiohttp.ClientError):
            pass
        self._authenticated = False

    async def _request(
        self,
        path: str,
        method: str = "GET",
        json_body: Optional[dict] = None,
        timeout: int = 10,
        _retry: bool = True,
    ) -> Optional[dict]:
        """Make an authenticated request to /Device/<path>."""
        url = f"{self._base_url}/Device/{path}"
        generation = self._auth_generation
        try:
            async with self._session.request(
                method,
                url,
                json=json_body,
                ssl=self._ssl,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status in _REAUTH_STATUSES and _retry:
                    _LOGGER.debug(
                        "Session expired for %s (HTTP %s), re-authenticating",
                        self.host,
                        response.status,
                    )
                    response.release()
                    await self._authenticate(generation)
                    return await self._request(
                        path, method, json_body, timeout=timeout, _retry=False
                    )
                if response.status in _REAUTH_STATUSES:
                    raise CrestronNVXAuthError("Device rejected the authenticated session")
                if response.status == 404 and method == "GET":
                    return None
                if response.status != 200:
                    raise CrestronNVXConnectionError(f"{path} returned HTTP {response.status}")
                data = await response.json(content_type=None)
                if not isinstance(data, dict):
                    raise CrestronNVXConnectionError(f"{path} did not return a JSON object")
                return data
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise CrestronNVXConnectionError(
                f"Request failed for {path}: {type(err).__name__}"
            ) from err

    async def longpoll(self, timeout: int = 30) -> Optional[dict]:
        """Block until a property changes, or return None on timeout/error.

        The device's own long-poll window is ~19s; callers should loop this
        rather than treat a single call as a persistent connection.
        """
        data = await self._request(LONGPOLL_PATH.removeprefix("/Device/"), timeout=timeout)
        if data is None:
            raise CrestronNVXConnectionError("Longpoll endpoint is unavailable")
        if data.get("Device") == "Response Timeout":
            return None
        return data

    async def get_device_info(self) -> Optional[dict]:
        """Model, serial number, firmware version, MAC address."""
        data = await self._request("DeviceInfo")
        try:
            return data["Device"]["DeviceInfo"]
        except (KeyError, TypeError):
            return None

    async def get_device_mode(self) -> Optional[str]:
        """ "Transmitter" or "Receiver", read from the device itself."""
        data = await self._request("DeviceSpecific/DeviceMode")
        try:
            return data["Device"]["DeviceSpecific"]["DeviceMode"]
        except (KeyError, TypeError):
            return None

    async def get_video_status(self) -> Optional[dict]:
        """Normalized video/HDCP status for this device's role.

        Transmitters report on their HDMI input (Inputs/0/Ports/0);
        receivers report on their HDMI output (Outputs/0/Ports/0). Only
        port index 0 exists on every model in the verified fleet.
        """
        if self.is_receiver:
            data = await self._request("AudioVideoInputOutput/Outputs/0/Ports/0")
            try:
                port = data["Device"]["AudioVideoInputOutput"]["Outputs"][0]["Ports"][0]
            except (KeyError, TypeError, IndexError):
                return None
            connected = port.get("IsSinkConnected", False)
        else:
            data = await self._request("AudioVideoInputOutput/Inputs/0/Ports/0")
            try:
                port = data["Device"]["AudioVideoInputOutput"]["Inputs"][0]["Ports"][0]
            except (KeyError, TypeError, IndexError):
                return None
            connected = port.get("IsSyncDetected", False)

        hdmi = port.get("Hdmi", {})
        return {
            "connected": connected,
            "horizontal_resolution": port.get("HorizontalResolution"),
            "vertical_resolution": port.get("VerticalResolution"),
            "frames_per_second": port.get("FramesPerSecond"),
            "hdcp_state": hdmi.get("HdcpState"),
            # Only meaningful on receivers - absent (None) on a transmitter's
            # HDMI input, which has no IsOutputDisabled field at all.
            "output_disabled": hdmi.get("IsOutputDisabled"),
        }

    async def get_ethernet_status(self) -> Optional[dict]:
        """Link status, IP address and MAC of the primary network adapter."""
        data = await self._request("Ethernet")
        try:
            adapter = data["Device"]["Ethernet"]["Adapters"][0]
        except (KeyError, TypeError, IndexError):
            return None
        try:
            ip_address = adapter["IPv4"]["Addresses"][0]["Address"]
        except (KeyError, TypeError, IndexError):
            ip_address = None
        return {
            "connected": adapter.get("LinkStatus", False),
            "ip_address": ip_address,
            "mac_address": adapter.get("MacAddress"),
        }

    async def get_discovered_streams(self) -> dict[str, dict]:
        """Network-wide map of {unique_id: stream_info} available to route to."""
        data = await self._request("DiscoveredStreams")
        try:
            streams = data["Device"]["DiscoveredStreams"]["Streams"]
            return streams if isinstance(streams, dict) else {}
        except (KeyError, TypeError):
            return {}

    async def get_current_route(self) -> Optional[dict]:
        """Current AvRouting route (VideoSource/AudioSource/UsbSource UIDs)."""
        data = await self._request("AvRouting/Routes/0")
        try:
            return data["Device"]["AvRouting"]["Routes"][0]
        except (KeyError, TypeError, IndexError):
            return None

    async def get_primary_stream(self) -> Optional[dict]:
        """Read primary receive state, excluding stream authentication fields."""
        data = await self._request("StreamReceive/Streams/0")
        if data is None:
            return None
        try:
            stream = data["Device"]["StreamReceive"]["Streams"][0]
        except (KeyError, TypeError, IndexError) as err:
            raise CrestronNVXConnectionError("Missing primary stream readback") from err
        if not isinstance(stream, dict) or not all(
            isinstance(stream.get(key), value_type)
            for key, value_type in (("StreamLocation", str), ("Processing", bool), ("Status", str))
        ):
            raise CrestronNVXConnectionError("Invalid primary stream readback")
        return {
            key: stream[key]
            for key in (
                "StreamLocation",
                "Status",
                "Processing",
                "CodecReady",
                "IsAutomaticInitiationEnabled",
            )
            if key in stream
        }

    async def set_route(self, source_uid: str) -> bool:
        """Route video and reconcile primary reception, preserving breakaway settings."""
        async with self._video_route_lock:
            try:
                async with asyncio.timeout(VIDEO_ROUTE_TIMEOUT):
                    return await self._set_verified_video_route(source_uid)
            except TimeoutError as err:
                raise CrestronNVXError("Timed out waiting for the NVX video route") from err

    async def _wait_primary_idle(self) -> Optional[dict]:
        """Do not send stream commands while the receiver is changing state."""
        for _ in range(20):
            stream = await self.get_primary_stream()
            if stream is None or stream.get("Processing") is False:
                return stream
            await asyncio.sleep(0.25)
        raise CrestronNVXError("The NVX receiver is still processing a stream change")

    async def _set_verified_video_route(self, source_uid: str) -> bool:
        stream = await self._wait_primary_idle()
        location = None
        if stream is not None:
            sources = await self.get_discovered_streams()
            source = sources.get(source_uid) or {}
            location = source.get("RtspUri") if isinstance(source, dict) else None
            try:
                uri = urlsplit(location) if isinstance(location, str) else None
                valid = uri is not None and uri.scheme in ("rtsp", "rtsps") and bool(uri.hostname)
            except ValueError:
                valid = False
            if not valid:
                raise CrestronNVXError("The selected NVX source has no valid discovered RTSP URL")
        body = {
            "Device": {
                "AvRouting": {
                    "Routes": [
                        {
                            "VideoSource": source_uid,
                        }
                    ]
                }
            }
        }
        if not self._post_ok(
            await self._request("AvRouting/Routes/0", method="POST", json_body=body)
        ):
            return False
        if stream is None:
            # Older firmware may omit StreamReceive; retain its routing path.
            route = await self.get_current_route()
            return bool(route and route.get("VideoSource") == source_uid)

        await asyncio.sleep(0.25)
        stream = await self._wait_primary_idle()
        if stream is None:
            raise CrestronNVXError("The NVX receiver did not return stream readback")
        if stream.get("StreamLocation") != location:
            # Some receivers acknowledge AvRouting without updating reception.
            # Only the primary RTSP subscription is changed, never AES67/USB.
            body = {"Device": {"StreamReceive": {"Streams": [{"StreamLocation": location}]}}}
            if not self._post_ok(
                await self._request("StreamReceive/Streams/0", method="POST", json_body=body)
            ):
                return False
        start_requested = False
        for _ in range(20):
            stream = await self.get_primary_stream()
            if (
                stream
                and stream.get("Processing") is False
                and stream.get("StreamLocation") == location
            ):
                if str(stream.get("Status", "")).casefold() == "stream started":
                    return True
                if stream.get("IsAutomaticInitiationEnabled") is False and not start_requested:
                    body = {"Device": {"StreamReceive": {"Streams": [{"Start": True}]}}}
                    if not self._post_ok(
                        await self._request(
                            "StreamReceive/Streams/0", method="POST", json_body=body
                        )
                    ):
                        return False
                    start_requested = True
            await asyncio.sleep(0.25)
        raise CrestronNVXError("The NVX receiver did not start the selected video stream")

    async def set_route_off(self) -> bool:
        """Clear the NVX video/audio/USB UID routes together.

        Unlike set_route(), this always clears all three regardless of the
        audio-follows-video setting: "Off" is a deliberate full blank, not a
        source switch. Direct NaxRx audio is separate and has its own Off control.
        """
        body = {
            "Device": {
                "AvRouting": {"Routes": [{"VideoSource": "", "AudioSource": "", "UsbSource": ""}]}
            }
        }
        async with self._video_route_lock:
            return self._post_ok(
                await self._request("AvRouting/Routes/0", method="POST", json_body=body)
            )

    async def get_route_control(self) -> Optional[dict]:
        """AvRouting-wide flags, notably IsSecondaryAudioFollowsVideoEnabled."""
        data = await self._request("AvRouting/RouteControl")
        try:
            return data["Device"]["AvRouting"]["RouteControl"]
        except (KeyError, TypeError):
            return None

    async def set_audio_follows_video(self, enabled: bool) -> bool:
        """Serialize follow changes with independent audio commands."""
        async with self._audio_route_lock:
            return await self._set_audio_follows_video(enabled)

    async def _set_audio_follows_video(self, enabled: bool) -> bool:
        """Toggle whether AudioSource auto-tracks VideoSource on future switches.

        When turning this on, also immediately syncs AudioSource to the
        current VideoSource - the flag only affects future video switches,
        so without this an already-independent audio source would keep
        playing until the next video change.
        """
        body = {
            "Device": {
                "AvRouting": {"RouteControl": {"IsSecondaryAudioFollowsVideoEnabled": enabled}}
            }
        }
        ok = self._post_ok(
            await self._request("AvRouting/RouteControl", method="POST", json_body=body)
        )
        if ok and enabled:
            route = await self.get_current_route()
            video_uid = (route or {}).get("VideoSource")
            if video_uid is not None:
                ok = await self._set_audio_source(video_uid)
        return ok

    async def set_audio_source(self, source_uid: str) -> bool:
        """Route audio only to the given DiscoveredStreams UID, independent of video."""
        async with self._audio_route_lock:
            return await self._set_audio_source(source_uid)

    async def _set_audio_source(self, source_uid: str) -> bool:
        body = {"Device": {"AvRouting": {"Routes": [{"AudioSource": source_uid}]}}}
        return self._post_ok(
            await self._request("AvRouting/Routes/0", method="POST", json_body=body)
        )

    async def _get_nax_streams(self, section: str, collection: str) -> dict:
        data = await self._request(f"NaxAudio/{section}")
        try:
            status = data["Device"]["NaxAudio"][section]
            streams = status[collection]
        except (KeyError, TypeError):
            return {}
        if not isinstance(streams, dict):
            return {}
        if section == "NaxRx" and status.get("MaxStreams", 1) != 1:
            return {}
        return {
            key: stream
            for key, stream in streams.items()
            if valid_key(key) and isinstance(stream, dict)
        }

    async def get_aes67_receivers(self) -> dict:
        """Read optional receive slots; unsupported firmware has no controls."""
        return await self._get_nax_streams("NaxRx", "NaxRxStreams")

    async def get_aes67_streams(self) -> dict:
        """Read the decoder's SAP/SDP discovery, without contacting the NAX."""
        return await self._get_nax_streams("NaxSdp", "NaxSdpStreams")

    async def _require_independent_audio(self) -> None:
        control = await self.get_route_control()
        if (control or {}).get("IsSecondaryAudioFollowsVideoEnabled") is not False:
            raise CrestronNVXError("Disable Audio Follows Video before routing AES67 audio")

    async def set_aes67_stream(self, receiver_id: str, discovery_id: str | None) -> bool:
        """Change only one AES67 receiver and verify actual reception, not the request."""
        async with self._audio_route_lock:
            try:
                async with asyncio.timeout(AUDIO_ROUTE_TIMEOUT):
                    return await self._set_aes67_stream(receiver_id, discovery_id)
            except TimeoutError as err:
                raise CrestronNVXError("Timed out confirming AES67 reception") from err

    async def _set_aes67_stream(self, receiver_id: str, discovery_id: str | None) -> bool:
        receivers = await self.get_aes67_receivers()
        if (
            not self.is_receiver
            or not valid_key(receiver_id)
            or single_receiver(receivers) != receiver_id
        ):
            raise CrestronNVXError("The single AES67 receiver is no longer available")
        await self._require_independent_audio()
        endpoint = None
        if discovery_id is None:
            change = {"StopRequested": True, "IsDisabled": True}
        else:
            if not valid_key(discovery_id):
                raise CrestronNVXError("Invalid AES67 discovery identifier")
            streams = await self.get_aes67_streams()
            stream = streams.get(discovery_id)
            if (
                not isinstance(stream, dict)
                or not compatible_stream(stream)
                or receivers[receiver_id].get("IsEncryptionEnabled") is not False
            ):
                raise CrestronNVXError("AES67 selection requires an unencrypted stereo 48 kHz feed")
            endpoint = stream_endpoint(stream)
            change = {
                "SessionNameRequested": stream["SessionNameStatus"],
                "NetworkAddressRequested": endpoint[0],
                "PortRequested": endpoint[1],
                "IsDisabled": False,
                "StartRequested": True,
            }
        body = {"Device": {"NaxAudio": {"NaxRx": {"NaxRxStreams": {receiver_id: change}}}}}
        if not self._post_ok(
            await self._request(
                f"NaxAudio/NaxRx/NaxRxStreams/{receiver_id}", method="POST", json_body=body
            )
        ):
            return False
        for _ in range(20):
            received = (await self.get_aes67_receivers()).get(receiver_id) or {}
            if endpoint is None:
                if (
                    received.get("StreamStatus") == "Stream Stopped"
                    and received.get("IsDisabled") is True
                ):
                    return True
            elif stream_started(received) and stream_endpoint(received) == endpoint:
                return True
            await asyncio.sleep(0.25)
        raise CrestronNVXError("The decoder did not confirm the selected AES67 receive state")

    async def set_audio_output_mode(self, value: str) -> bool:
        """Choose output audio without changing video or configuring any transmitter."""
        async with self._audio_route_lock:
            try:
                async with asyncio.timeout(AUDIO_ROUTE_TIMEOUT):
                    if not self.is_receiver or value not in AUDIO_OUTPUT_MODES.values():
                        raise CrestronNVXError("Invalid audio output mode")
                    specific = await self.get_device_specific()
                    if (specific or {}).get("AudioSource") not in AUDIO_OUTPUT_MODES.values():
                        raise CrestronNVXError("Audio output mode is not supported by this device")
                    if value == "SecondaryStreamAudio":
                        await self._require_independent_audio()
                    body = {"Device": {"DeviceSpecific": {"AudioSource": value}}}
                    if not self._post_ok(
                        await self._request("DeviceSpecific", method="POST", json_body=body)
                    ):
                        return False
                    for _ in range(20):
                        status = await self.get_device_specific()
                        if (status or {}).get("AudioSource") == value:
                            return True
                        await asyncio.sleep(0.25)
                    raise CrestronNVXError("The decoder did not confirm the audio output mode")
            except TimeoutError as err:
                raise CrestronNVXError("Timed out confirming audio output mode") from err

    async def get_device_specific(self) -> Optional[dict]:
        """DeviceSpecific object - VideoSource/ActiveVideoSource used for HDMI input switching."""
        data = await self._request("DeviceSpecific")
        try:
            specific = data["Device"]["DeviceSpecific"]
            return specific if isinstance(specific, dict) else None
        except (KeyError, TypeError):
            return None

    async def set_video_source(self, value: str) -> bool:
        """Set DeviceSpecific.VideoSource - e.g. "Stream", "Input1", "Input2".

        Used both for a receiver's local-HDMI-vs-stream switch and a
        multi-input transmitter's HDMI input switch - same field either way.
        """
        body = {"Device": {"DeviceSpecific": {"VideoSource": value}}}
        return self._post_ok(await self._request("DeviceSpecific", method="POST", json_body=body))

    async def get_osd(self) -> Optional[dict]:
        """OSD state (Text, IsEnabled, Location, ...), or None if unsupported.

        Not every model has an OSD - unsupported devices return the literal
        string "UNSUPPORTED PROPERTY, CHECK REST API!!!" in place of the
        object rather than a normal error, so a dict/non-dict check is what
        distinguishes "supported but empty" from "not supported at all".
        """
        data = await self._request("Osd")
        try:
            osd = data["Device"]["Osd"]
        except (KeyError, TypeError):
            return None
        return osd if isinstance(osd, dict) else None

    async def set_osd(self, *, text: Optional[str] = None, enabled: Optional[bool] = None) -> bool:
        """Set OSD text and/or enabled state - only the given fields are written."""
        fields = {}
        if text is not None:
            fields["Text"] = text
        if enabled is not None:
            fields["IsEnabled"] = enabled
        if not fields:
            return True
        body = {"Device": {"Osd": fields}}
        return self._post_ok(await self._request("Osd", method="POST", json_body=body))

    async def get_test_pattern(self) -> Optional[str]:
        """Currently active test pattern on Output1, or None if unsupported/unknown."""
        data = await self._request("TestPatternConfig/Outputs/Output1/CurrentTestPattern")
        try:
            return data["Device"]["TestPatternConfig"]["Outputs"]["Output1"]["CurrentTestPattern"]
        except (KeyError, TypeError):
            return None

    async def set_test_pattern(self, pattern: str) -> bool:
        """Set the active test pattern on Output1 - "Off" restores the real source.

        Confirmed live on a DM-NVX-E30 transmitter: applies immediately (no
        read-after-write lag like Osd) and cleanly reverts.
        """
        body = {
            "Device": {
                "TestPatternConfig": {"Outputs": {"Output1": {"CurrentTestPattern": pattern}}}
            }
        }
        return self._post_ok(
            await self._request(
                "TestPatternConfig/Outputs/Output1/CurrentTestPattern",
                method="POST",
                json_body=body,
            )
        )

    async def set_output_disabled(self, disabled: bool) -> bool:
        """Force-disable (blank) or re-enable a receiver's physical HDMI output.

        Independent of AvRouting - this blanks the output itself rather than
        clearing the routed source underneath, so the route is preserved and
        resumes as soon as the output is re-enabled. Confirmed live on a
        receiver: the write is accepted immediately but takes a couple of
        seconds to actually propagate (same read-after-write lag as Osd) -
        an immediate readback can still show the old state.
        """
        body = {
            "Device": {
                "AudioVideoInputOutput": {
                    "Outputs": [{"Ports": [{"Hdmi": {"IsOutputDisabled": disabled}}]}]
                }
            }
        }
        return self._post_ok(
            await self._request(
                "AudioVideoInputOutput/Outputs/0/Ports/0/Hdmi/IsOutputDisabled",
                method="POST",
                json_body=body,
            )
        )

    async def get_preview_image(self, size: str = "540px") -> Optional[bytes]:
        """Fetch a JPEG snapshot of what this device currently shows.

        Not under /Device/ like everything else - it's a plain authenticated
        file at /preview/preview_<size>.jpeg on the same cookie session.
        Confirmed live: 401 without valid session cookies, and a stale
        session redirects to the login page the same way /Device/ requests
        do (see _REAUTH_STATUSES on _request), so the same handling applies
        here rather than reusing _request itself, which is JSON-only.
        """
        url = f"{self._base_url}/preview/preview_{size}.jpeg"
        for attempt in (1, 2):
            generation = self._auth_generation
            try:
                async with self._session.get(
                    url,
                    ssl=self._ssl,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status in _REAUTH_STATUSES and attempt == 1:
                        response.release()
                        await self._authenticate(generation)
                        continue
                    if response.status in _REAUTH_STATUSES:
                        raise CrestronNVXAuthError("Device rejected the preview session")
                    if response.status != 200:
                        return None
                    image = await response.read()
                    return image if image.startswith(b"\xff\xd8\xff") else None
            except TimeoutError:
                _LOGGER.error("Timeout fetching preview image for %s", self.host)
                return None
            except aiohttp.ClientError as err:
                _LOGGER.error("Error fetching preview image for %s: %s", self.host, err)
                return None
        return None

    async def get_identify(self) -> Optional[bool]:
        """Return whether identify mode is active, or None if unsupported."""
        data = await self._request("Identify")
        try:
            value = data["Device"]["Identify"]["IsIdentifyActive"]
            return value if isinstance(value, bool) else None
        except (KeyError, TypeError):
            return None

    async def set_identify(self, enabled: bool) -> bool:
        """Turn the device's identify LED flashing mode on or off."""
        body = {"Device": {"Identify": {"IsIdentifyActive": enabled}}}
        return self._post_ok(await self._request("Identify", method="POST", json_body=body))

    @staticmethod
    def _post_ok(result: Optional[dict]) -> bool:
        if not result:
            return False
        try:
            actions = result["Actions"]
        except (KeyError, TypeError):
            return False

        if not isinstance(actions, list):
            return False
        saw_result = False
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get("Results"), list):
                return False
            if not action["Results"]:
                return False
            for item in action["Results"]:
                if not isinstance(item, dict):
                    return False
                saw_result = True
                status_id = item.get("StatusId")
                if status_id != 0:
                    _LOGGER.error(
                        "NVX POST failed: path=%s property=%s status=%s info=%s",
                        item.get("Path"),
                        item.get("Property"),
                        status_id,
                        item.get("StatusInfo"),
                    )
                    return False
        return saw_result

    async def get_cec_input_message(self) -> Optional[str]:
        """Raw base64 CEC frame most recently received on the HDMI input."""
        data = await self._request("AudioVideoInputOutput/Inputs/0/Ports/0/Hdmi/ReceiveCecMessage")
        try:
            return data["Device"]["AudioVideoInputOutput"]["Inputs"][0]["Ports"][0]["Hdmi"][
                "ReceiveCecMessage"
            ]
        except (KeyError, TypeError, IndexError):
            return None


class CrestronNVXAPI:
    """Owns the shared HTTPS session/cookie jar and the devices on it."""

    def __init__(
        self, verify_ssl: bool = False, session: aiohttp.ClientSession | None = None
    ) -> None:
        self.devices: dict[str, CrestronNVXDevice] = {}
        self._verify_ssl = verify_ssl
        self._owns_session = session is None
        if session is None:
            # unsafe=True is required: aiohttp's default cookie jar refuses to
            # store cookies for bare IP-address hosts (no registrable domain),
            # which is exactly what every NVX device is addressed by.
            session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self._session = session

    async def add_device(
        self, host: str, name: str, username: str, password: str
    ) -> CrestronNVXDevice:
        """Create, authenticate and register a device."""
        device = CrestronNVXDevice(
            host=host,
            username=username,
            password=password,
            session=self._session,
            verify_ssl=self._verify_ssl,
            name=name,
        )
        try:
            await device.login()
        except BaseException:
            await device.logout()
            raise
        self.devices[name] = device
        _LOGGER.info("Added %s device: %s at %s", device.device_mode or "unknown", name, host)
        return device

    def get_device(self, name: str) -> Optional[CrestronNVXDevice]:
        return self.devices.get(name)

    async def close(self) -> None:
        for device in self.devices.values():
            await device.logout()
        if self._owns_session:
            await self._session.close()
