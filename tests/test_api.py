"""Regression tests for auth, protocol failures, routing and CEC decoding."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.crestron_nvx.crestron_nvx_api import (
    CrestronNVXAPI,
    CrestronNVXAuthError,
    CrestronNVXConnectionError,
    CrestronNVXDevice,
    decode_cec_message,
)


class Response:
    def __init__(self, status=200, data=None, body=b""):
        self.status, self.data, self.body = status, data, body
        self.released = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.released = True

    def release(self):
        self.released = True

    async def json(self, **_):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    async def read(self):
        return self.body


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403])
async def test_bad_credentials_probe_is_bounded(device, status):
    session = device._session
    session.get.return_value = Response()
    session.post.return_value = Response()
    session.request.return_value = Response(status)
    with pytest.raises(CrestronNVXAuthError):
        await device.login()
    assert session.post.call_count == 1
    assert session.request.call_count == 1
    assert not device._authenticated


async def test_login_503_is_connection_failure(device):
    device._session.get.return_value = Response()
    device._session.post.return_value = Response(503)
    with pytest.raises(CrestronNVXConnectionError):
        await device.login()


async def test_concurrent_expiry_only_renews_once(device):
    device._session.request.side_effect = lambda *_a, **_k: (
        Response(403) if device._auth_generation == 0 else Response(data={"Device": {}})
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def login_session():
        started.set()
        await release.wait()

    device._login_session = AsyncMock(side_effect=login_session)
    # The validation probe is the only non-retrying request during renewal.
    original = device._request

    async def request(*args, **kwargs):
        if kwargs.get("_retry") is False and args[0] == "DeviceSpecific/DeviceMode":
            return {"Device": {"DeviceSpecific": {"DeviceMode": "Receiver"}}}
        return await original(*args, **kwargs)

    device._request = request
    first = asyncio.create_task(device._request("Ethernet"))
    await started.wait()
    second = asyncio.create_task(device._request("Ethernet"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    device._login_session.assert_awaited_once()


@pytest.mark.parametrize(
    "response",
    [Response(500), Response(data=ValueError("html")), Response(data=[]), Response(data="bad")],
)
async def test_invalid_responses_raise_connection_error(device, response):
    device._session.request.return_value = response
    with pytest.raises(CrestronNVXConnectionError):
        await device.get_video_status()


@pytest.mark.parametrize("error", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_transport_failure_is_not_empty_status(device, error):
    device._session.request.side_effect = error
    with pytest.raises(CrestronNVXConnectionError):
        await device.get_ethernet_status()


async def test_unsupported_capability_and_idle_longpoll(device):
    device._session.request.return_value = Response(404)
    assert await device.get_identify() is None
    device._session.request.return_value = Response(
        data={"Device": {"Identify": {"IsIdentifyActive": False}}}
    )
    await device._load_identify_supported()
    assert device.identify_supported
    device._session.request.return_value = Response(data={"Device": "Response Timeout"})
    assert await device.longpoll() is None


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"Actions": [{"Results": [{"StatusId": 0}]}]}, True),
        ({"Actions": [{"Results": [{"StatusId": 0}, {"StatusId": 1}]}]}, False),
        ({"Actions": [{"Results": [{"StatusId": 0}]}, {"Results": [{"StatusId": 2}]}]}, False),
        ({"Actions": []}, False),
        ({"Actions": None}, False),
        ({"Actions": [None]}, False),
        ({"Actions": [{"Results": [None]}]}, False),
        ({"Actions": [{"Results": []}]}, False),
        (None, False),
    ],
)
def test_all_post_results_must_succeed(result, expected):
    assert CrestronNVXDevice._post_ok(result) is expected


async def test_video_routing_preserves_breakaway_and_off_clears_all(device):
    device._request = AsyncMock(return_value={"Actions": [{"Results": [{"StatusId": 0}]}]})
    assert await device.set_route("source")
    assert device._request.call_args.kwargs["json_body"]["Device"]["AvRouting"]["Routes"] == [
        {"VideoSource": "source"}
    ]
    assert await device.set_route_off()
    assert device._request.call_args.kwargs["json_body"]["Device"]["AvRouting"]["Routes"] == [
        {"VideoSource": "", "AudioSource": "", "UsbSource": ""}
    ]


async def test_audio_follow_syncs_even_when_video_is_off(device):
    device._request = AsyncMock(return_value={"Actions": [{"Results": [{"StatusId": 0}]}]})
    device.get_current_route = AsyncMock(return_value={"VideoSource": ""})
    device.set_audio_source = AsyncMock(return_value=True)
    assert await device.set_audio_follows_video(True)
    device.set_audio_source.assert_awaited_once_with("")


@pytest.mark.parametrize(
    "body, expected", [(b"\xff\xd8\xffimage", True), (b"<html>login</html>", False)]
)
async def test_preview_must_be_jpeg(device, body, expected):
    device._session.get.return_value = Response(body=body)
    assert bool(await device.get_preview_image()) is expected


async def test_borrowed_session_is_not_closed():
    session = MagicMock(close=AsyncMock())
    api = CrestronNVXAPI(session=session)
    await api.close()
    session.close.assert_not_awaited()


@pytest.mark.parametrize(
    "frame, expected",
    [
        (b"\x40\x44\x41", "volume_up"),
        (b"\x40\x44\x42", "volume_down"),
        (b"\x40\x44\x43", "mute"),
        (b"\x40\x04", "power_on"),
        (b"\x40\x36", "power_off"),
        (b"\x40\x45", None),
        (b"\x40", None),
    ],
)
def test_cec_frames(frame, expected):
    assert decode_cec_message(base64.b64encode(frame).decode()) == expected


@pytest.mark.parametrize("value", [None, "", "bad!", "%%%"])
def test_invalid_cec_frames(value):
    assert decode_cec_message(value) is None
