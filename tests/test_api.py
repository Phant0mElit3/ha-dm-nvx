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
    CrestronNVXError,
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
    device.get_primary_stream = AsyncMock(return_value=None)
    device.get_current_route = AsyncMock(return_value={"VideoSource": "source"})
    assert await device.set_route("source")
    assert device._request.call_args.kwargs["json_body"]["Device"]["AvRouting"]["Routes"] == [
        {"VideoSource": "source"}
    ]
    assert await device.set_route_off()
    assert device._request.call_args.kwargs["json_body"]["Device"]["AvRouting"]["Routes"] == [
        {"VideoSource": "", "AudioSource": "", "UsbSource": ""}
    ]


@pytest.fixture
def routing_device(device, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    device._request = AsyncMock(return_value={"Actions": [{"Results": [{"StatusId": 0}]}]})
    device.get_discovered_streams = AsyncMock(
        return_value={"new": {"RtspUri": "rtsp://192.0.2.2:554/live.sdp"}}
    )
    return device


def receive_state(location="rtsp://192.0.2.2:554/live.sdp", **fields):
    return {"StreamLocation": location, "Status": "Stream started", "Processing": False, **fields}


async def test_route_acknowledged_but_stream_unchanged_uses_rtsp_fallback(routing_device):
    device = routing_device
    old = receive_state("rtsp://192.0.2.1:554/live.sdp")
    device.get_primary_stream = AsyncMock(side_effect=[old, old, receive_state(), receive_state()])
    assert await device.set_route("new")
    posts = device._request.call_args_list
    assert len(posts) == 2
    assert posts[0].kwargs["json_body"]["Device"]["AvRouting"]["Routes"] == [{"VideoSource": "new"}]
    assert posts[1].args == ("StreamReceive/Streams/0",)
    assert posts[1].kwargs["json_body"] == {
        "Device": {
            "StreamReceive": {"Streams": [{"StreamLocation": "rtsp://192.0.2.2:554/live.sdp"}]}
        }
    }


async def test_working_avrouting_does_not_rewrite_stream(routing_device):
    device = routing_device
    device.get_primary_stream = AsyncMock(return_value=receive_state())
    assert await device.set_route("new")
    assert device._request.call_count == 1


async def test_failed_fallback_is_not_success(routing_device):
    device = routing_device
    device.get_primary_stream = AsyncMock(return_value=receive_state("rtsp://192.0.2.1/live.sdp"))
    device._request.side_effect = [
        {"Actions": [{"Results": [{"StatusId": 0}]}]},
        {"Actions": [{"Results": [{"StatusId": 1}]}]},
    ]
    assert not await device.set_route("new")


async def test_stale_receive_readback_raises_after_bounded_poll(routing_device):
    device = routing_device
    device.get_primary_stream = AsyncMock(return_value=receive_state("rtsp://192.0.2.1/live.sdp"))
    with pytest.raises(CrestronNVXError, match="did not start"):
        await device.set_route("new")
    assert device.get_primary_stream.call_count == 22
    assert device._request.call_count == 2


async def test_busy_receiver_never_receives_route_commands(routing_device):
    device = routing_device
    device.get_primary_stream = AsyncMock(return_value=receive_state(Processing=True))
    with pytest.raises(CrestronNVXError, match="still processing"):
        await device.set_route("new")
    device._request.assert_not_called()
    assert device.get_primary_stream.call_count == 20


async def test_busy_transition_settles_without_fallback(routing_device):
    device = routing_device
    device.get_primary_stream = AsyncMock(
        side_effect=[
            receive_state(),
            receive_state(Processing=True),
            receive_state(),
            receive_state(),
        ]
    )
    assert await device.set_route("new")
    assert device._request.call_count == 1


async def test_manual_initiation_starts_without_changing_mode(routing_device):
    device = routing_device
    stopped = receive_state(Status="Stream stopped", IsAutomaticInitiationEnabled=False)
    device.get_primary_stream = AsyncMock(side_effect=[stopped, stopped, stopped, receive_state()])
    assert await device.set_route("new")
    assert device._request.call_args.kwargs["json_body"] == {
        "Device": {"StreamReceive": {"Streams": [{"Start": True}]}}
    }


@pytest.mark.parametrize(
    "uri", [None, "", "https://192.0.2.2/live.sdp", "rtsp:///live.sdp", "rtsp://["]
)
async def test_missing_or_invalid_stream_url_does_not_write(routing_device, uri):
    device = routing_device
    device.get_primary_stream = AsyncMock(return_value=receive_state())
    device.get_discovered_streams.return_value = {"new": {"RtspUri": uri}}
    with pytest.raises(CrestronNVXError, match="valid discovered RTSP"):
        await device.set_route("new")
    device._request.assert_not_called()


async def test_primary_readback_omits_stream_credentials(device):
    device._request = AsyncMock(
        return_value={
            "Device": {
                "StreamReceive": {
                    "Streams": [{**receive_state(), "Username": "secret", "Password": "secret"}]
                }
            }
        }
    )
    assert await device.get_primary_stream() == receive_state()


@pytest.mark.parametrize("data", [{}, {"Device": {"StreamReceive": {"Streams": [{}]}}}])
async def test_invalid_primary_readback_is_not_unsupported(device, data):
    device._request = AsyncMock(return_value=data)
    with pytest.raises(CrestronNVXConnectionError, match="primary stream readback"):
        await device.get_primary_stream()


async def test_video_route_deadline_releases_lock(device, monkeypatch):
    monkeypatch.setattr(
        "custom_components.crestron_nvx.crestron_nvx_api.VIDEO_ROUTE_TIMEOUT", 0.001
    )
    device._set_verified_video_route = AsyncMock(side_effect=lambda _uid: None)

    async def stalled(_uid):
        await asyncio.Event().wait()

    device._set_verified_video_route.side_effect = stalled
    with pytest.raises(CrestronNVXError, match="Timed out"):
        await device.set_route("new")
    assert not device._video_route_lock.locked()


async def test_off_waits_for_inflight_video_change(device):
    started, finish = asyncio.Event(), asyncio.Event()

    async def route(_uid):
        started.set()
        await finish.wait()
        return True

    device._set_verified_video_route = route
    device._request = AsyncMock(return_value={"Actions": [{"Results": [{"StatusId": 0}]}]})
    changing = asyncio.create_task(device.set_route("new"))
    await started.wait()
    off = asyncio.create_task(device.set_route_off())
    await asyncio.sleep(0)
    device._request.assert_not_called()
    finish.set()
    assert await changing
    assert await off


async def test_rejected_avroute_never_attempts_fallback(routing_device):
    device = routing_device
    device.get_primary_stream = AsyncMock(return_value=receive_state())
    device._request.return_value = {"Actions": [{"Results": [{"StatusId": 1}]}]}
    assert not await device.set_route("new")
    assert device._request.call_count == 1


async def test_delayed_manual_stream_starts_only_after_new_location_settles(routing_device):
    device = routing_device
    old = receive_state("rtsp://192.0.2.1/live.sdp", IsAutomaticInitiationEnabled=False)
    stopped = receive_state(Status="Stream stopped", IsAutomaticInitiationEnabled=False)
    device.get_primary_stream = AsyncMock(
        side_effect=[
            old,
            old,
            old,
            receive_state(Processing=True),
            stopped,
            stopped,
            receive_state(),
        ]
    )
    assert await device.set_route("new")
    posts = device._request.call_args_list
    assert len(posts) == 3
    assert posts[-1].kwargs["json_body"] == {
        "Device": {"StreamReceive": {"Streams": [{"Start": True}]}}
    }


async def test_audio_follow_syncs_even_when_video_is_off(device):
    device._request = AsyncMock(return_value={"Actions": [{"Results": [{"StatusId": 0}]}]})
    device.get_current_route = AsyncMock(return_value={"VideoSource": ""})
    assert await device.set_audio_follows_video(True)
    device._request.assert_any_await(
        "AvRouting/Routes/0",
        method="POST",
        json_body={"Device": {"AvRouting": {"Routes": [{"AudioSource": ""}]}}},
    )


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
