"""AES67 routing safeguards and readback using the observed D30 object shape."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.crestron_nvx import CrestronNVXDataUpdateCoordinator
from custom_components.crestron_nvx.aes67 import (
    compatible_stream,
    single_receiver,
    stream_options,
)
from custom_components.crestron_nvx.const import DOMAIN
from custom_components.crestron_nvx.crestron_nvx_api import CrestronNVXError
from custom_components.crestron_nvx.select import (
    CrestronNVXAes67Select,
    CrestronNVXAudioOutputSelect,
    async_setup_entry,
)

SUCCESS = {"Actions": [{"Results": [{"StatusId": 0}]}]}
STREAM = {
    "SessionNameStatus": "MediaStream19c4.42.68.3f.bc.16",
    "NetworkAddressStatus": "239.8.0.0",
    "PortStatus": 5004,
    "SourceNetworkAddress": "192.0.2.80",
    "ChannelsNum": 2,
    "EncodingFormat": "L24",
    "EncodingSampleRate": 48000,
    "IsEncryptionEnabled": False,
}
STOPPED = {
    "StreamStatus": "Stream Stopped",
    "NetworkAddressStatus": "0.0.0.0",
    "PortStatus": 5004,
    "SessionNameStatus": STREAM["SessionNameStatus"],
    "IsDisabled": False,
    "IsEncryptionEnabled": False,
    "ErrCode": "OK",
}
STARTED = {**STOPPED, **STREAM, "StreamStatus": "Stream Started"}
CONTROL = {"IsSecondaryAudioFollowsVideoEnabled": False}


@pytest.fixture
def audio_device(device):
    device._request = AsyncMock(return_value=SUCCESS)
    device.get_aes67_receivers = AsyncMock(return_value={"Stream01": STOPPED})
    device.get_aes67_streams = AsyncMock(return_value={"123": STREAM})
    device.get_route_control = AsyncMock(return_value=CONTROL)
    device.get_device_specific = AsyncMock(return_value={"AudioSource": "AudioFollowsVideo"})
    return device


@pytest.fixture
def audio_coordinator(coordinator):
    coordinator.data = {
        "aes67_receivers": {"Stream01": STOPPED},
        "aes67_streams": {"123": STREAM},
        "route_control": CONTROL.copy(),
        "device_specific": {"AudioSource": "AudioFollowsVideo"},
    }
    return coordinator


@pytest.mark.parametrize(
    "change",
    [
        {"ChannelsNum": 8},
        {"EncodingSampleRate": 96000},
        {"EncodingFormat": "L16"},
        {"IsEncryptionEnabled": True},
        {"IsEncryptionEnabled": None},
        {"NetworkAddressStatus": "192.0.2.80"},
        {"NetworkAddressStatus": "ff02::1"},
        {"PortStatus": True},
        {"PortStatus": "5004"},
        {"PortStatus": 1024},
        {"PortStatus": 65536},
        {"SessionNameStatus": "x" * 32},
        {"SessionNameStatus": "-invalid"},
        {"SessionNameStatus": " "},
        {"SessionNameStatus": "bad\nname"},
    ],
)
def test_incompatible_streams_are_not_offered(change):
    assert not compatible_stream({**STREAM, **change})
    assert stream_options({"123": {**STREAM, **change}}) == {"Off": None}


def test_duplicate_names_and_unsafe_keys():
    options = stream_options({"1": STREAM, "2": STREAM, "../3": STREAM})
    assert len(options) == 3
    assert list(options.values()) == [None, "1", "2"]
    assert all("192.0.2.80" in label for label in list(options)[1:])
    assert compatible_stream({**STREAM, "SessionNameStatus": "S/PDIF24c4.42.68.3f.bc.16"})
    assert single_receiver({"Stream01": STOPPED}) == "Stream01"
    assert single_receiver({"Stream01": STOPPED, "Stream02": STOPPED}) is None
    assert single_receiver({"../Stream01": STOPPED}) is None


async def test_select_uses_fresh_discovery_and_actual_receive_readback(audio_device):
    audio_device.get_aes67_receivers.side_effect = [
        {"Stream01": STOPPED},
        {"Stream01": {**STOPPED, "NetworkAddressRequested": "239.8.0.0"}},
        {"Stream01": STARTED},
    ]
    with patch("custom_components.crestron_nvx.crestron_nvx_api.asyncio.sleep", new=AsyncMock()):
        assert await audio_device.set_aes67_stream("Stream01", "123")
    audio_device._request.assert_awaited_once_with(
        "NaxAudio/NaxRx/NaxRxStreams/Stream01",
        method="POST",
        json_body={
            "Device": {
                "NaxAudio": {
                    "NaxRx": {
                        "NaxRxStreams": {
                            "Stream01": {
                                "SessionNameRequested": STREAM["SessionNameStatus"],
                                "NetworkAddressRequested": "239.8.0.0",
                                "PortRequested": 5004,
                                "IsDisabled": False,
                                "StartRequested": True,
                            }
                        }
                    }
                }
            }
        },
    )
    assert audio_device.get_aes67_receivers.await_count == 3


@pytest.mark.parametrize("control", [{}, None, {"IsSecondaryAudioFollowsVideoEnabled": True}])
async def test_follow_guard_is_checked_again_before_post(audio_device, control):
    audio_device.get_route_control.return_value = control
    with pytest.raises(CrestronNVXError, match="Disable Audio Follows Video"):
        await audio_device.set_aes67_stream("Stream01", "123")
    audio_device._request.assert_not_called()


@pytest.mark.parametrize(
    "receiver_id, discovery_id",
    [("../x", "123"), (None, "123"), ("Stream01", "../x"), ("Stream01", "missing")],
)
async def test_missing_or_invalid_stream_rejected(audio_device, receiver_id, discovery_id):
    with pytest.raises(CrestronNVXError):
        await audio_device.set_aes67_stream(receiver_id, discovery_id)
    audio_device._request.assert_not_called()


async def test_encryption_never_disabled_implicitly(audio_device):
    audio_device.get_aes67_receivers.return_value = {
        "Stream01": {**STOPPED, "IsEncryptionEnabled": True}
    }
    with pytest.raises(CrestronNVXError, match="unencrypted"):
        await audio_device.set_aes67_stream("Stream01", "123")
    audio_device._request.assert_not_called()


async def test_off_only_stops_receive_slot(audio_device):
    audio_device.get_aes67_receivers.side_effect = [
        {"Stream01": STARTED},
        {"Stream01": {**STOPPED, "IsDisabled": True}},
    ]
    assert await audio_device.set_aes67_stream("Stream01", None)
    assert audio_device._request.call_args.kwargs["json_body"] == {
        "Device": {
            "NaxAudio": {
                "NaxRx": {
                    "NaxRxStreams": {
                        "Stream01": {
                            "StopRequested": True,
                            "IsDisabled": True,
                        }
                    }
                }
            }
        }
    }
    audio_device.get_aes67_streams.assert_not_called()


async def test_rejected_post_is_failure(audio_device):
    audio_device._request.return_value = {"Actions": [{"Results": [{"StatusId": 1}]}]}
    assert not await audio_device.set_aes67_stream("Stream01", "123")


async def test_no_reception_is_error_even_after_accepted_post(audio_device):
    with patch("custom_components.crestron_nvx.crestron_nvx_api.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(CrestronNVXError, match="did not confirm"):
            await audio_device.set_aes67_stream("Stream01", "123")


async def test_output_mode_preserves_video_and_requires_readback(audio_device):
    audio_device.get_device_specific.side_effect = [
        {"AudioSource": "AudioFollowsVideo"},
        {"AudioSource": "SecondaryStreamAudio"},
    ]
    assert await audio_device.set_audio_output_mode("SecondaryStreamAudio")
    audio_device._request.assert_awaited_once_with(
        "DeviceSpecific",
        method="POST",
        json_body={"Device": {"DeviceSpecific": {"AudioSource": "SecondaryStreamAudio"}}},
    )


async def test_output_mode_does_not_take_over_follow(audio_device):
    audio_device.get_route_control.return_value = {"IsSecondaryAudioFollowsVideoEnabled": True}
    with pytest.raises(CrestronNVXError, match="Disable Audio Follows Video"):
        await audio_device.set_audio_output_mode("SecondaryStreamAudio")
    audio_device._request.assert_not_called()


async def test_output_mode_accepted_but_not_applied_is_error(audio_device):
    with patch("custom_components.crestron_nvx.crestron_nvx_api.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(CrestronNVXError, match="did not confirm"):
            await audio_device.set_audio_output_mode("PrimaryStreamAudio")


async def test_audio_commands_share_lock(audio_device):
    await audio_device._audio_route_lock.acquire()
    task = asyncio.create_task(audio_device.set_audio_output_mode("AudioFollowsVideo"))
    try:
        await asyncio.sleep(0)
        audio_device._request.assert_not_called()
    finally:
        audio_device._audio_route_lock.release()
    assert await task


def test_dropdown_does_not_report_requested_or_stale_session(device, audio_coordinator):
    entity = CrestronNVXAes67Select(audio_coordinator, device)
    assert entity.current_option == "Off"
    audio_coordinator.data["aes67_receivers"]["Stream01"] = {
        **STOPPED,
        "StreamStatus": "Connecting",
        "NetworkAddressRequested": "239.8.0.0",
    }
    assert entity.current_option is None
    audio_coordinator.data["aes67_receivers"]["Stream01"] = STARTED
    assert entity.current_option == list(entity.options)[1]
    audio_coordinator.data["aes67_streams"]["456"] = STREAM
    assert entity.current_option is None
    audio_coordinator.data["route_control"]["IsSecondaryAudioFollowsVideoEnabled"] = True
    assert not entity.available


async def test_failed_select_refreshes_state(device, audio_coordinator):
    entity = CrestronNVXAes67Select(audio_coordinator, device)
    device.set_aes67_stream = AsyncMock(side_effect=CrestronNVXError("not received"))
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option(entity.options[1])
    audio_coordinator.async_request_refresh.assert_awaited_once()
    assert entity.current_option == "Off"


async def test_polling_reads_audio_on_d30_without_hdmi_inputs(hass, audio_device):
    audio_device.hdmi_inputs = 0
    audio_device.get_video_status = AsyncMock(return_value={})
    audio_device.get_ethernet_status = AsyncMock(return_value={})
    audio_device.get_discovered_streams = AsyncMock(return_value={})
    audio_device.get_current_route = AsyncMock(return_value={})
    audio_device.get_primary_stream = AsyncMock(return_value={})
    coordinator = CrestronNVXDataUpdateCoordinator(hass, audio_device, timedelta(seconds=30))
    data = await coordinator._async_update_data()
    assert data["device_specific"]["AudioSource"] == "AudioFollowsVideo"
    assert data["aes67_receivers"] == {"Stream01": STOPPED}
    assert data["aes67_streams"] == {"123": STREAM}
    audio_device.get_aes67_receivers.return_value = {}
    audio_device.get_aes67_streams.reset_mock()
    data = await coordinator._async_update_data()
    assert "aes67_streams" not in data
    audio_device.get_aes67_streams.assert_not_called()


async def test_controls_discovered_later_without_duplicates(hass, device, audio_coordinator):
    entry = MagicMock(entry_id="test")
    coordinator = audio_coordinator
    coordinator.async_add_listener = MagicMock()
    status = coordinator.data
    coordinator.data = {}
    hass.data[DOMAIN] = {
        "test": {
            "coordinators": {device.name: coordinator},
            "api": SimpleNamespace(get_device=lambda name: device),
        }
    }
    entities = []
    await async_setup_entry(hass, entry, entities.extend)
    assert not any(isinstance(entity, CrestronNVXAes67Select) for entity in entities)
    listener = coordinator.async_add_listener.call_args.args[0]
    coordinator.data = status
    listener()
    listener()
    assert sum(isinstance(entity, CrestronNVXAes67Select) for entity in entities) == 1
    assert sum(isinstance(entity, CrestronNVXAudioOutputSelect) for entity in entities) == 1
    entry.async_on_unload.assert_called_once()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"Device": {"NaxAudio": "Unsupported"}},
        {"Device": {"NaxAudio": {"NaxRx": {"NaxRxStreams": []}}}},
    ],
)
async def test_unsupported_receive_objects(device, payload):
    device._request = AsyncMock(return_value=payload)
    assert await device.get_aes67_receivers() == {}


async def test_live_shape_parsed_and_multi_slot_mapping_not_guessed(device):
    status = {"MaxStreams": 1, "NaxRxStreams": {"Stream01": STOPPED}}
    device._request = AsyncMock(return_value={"Device": {"NaxAudio": {"NaxRx": status}}})
    assert await device.get_aes67_receivers() == {"Stream01": STOPPED}
    device._request.assert_awaited_once_with("NaxAudio/NaxRx")
    status["MaxStreams"] = 2
    assert await device.get_aes67_receivers() == {}


@pytest.mark.parametrize("operation", ["receive", "output"])
async def test_command_deadline_and_lock_release(audio_device, operation):
    async def slow_read():
        await asyncio.Event().wait()

    if operation == "receive":
        audio_device.get_aes67_receivers = slow_read
        command = audio_device.set_aes67_stream("Stream01", "123")
    else:
        audio_device.get_device_specific = slow_read
        command = audio_device.set_audio_output_mode("AudioFollowsVideo")
    with patch("custom_components.crestron_nvx.crestron_nvx_api.AUDIO_ROUTE_TIMEOUT", 0.01):
        with pytest.raises(CrestronNVXError, match="Timed out"):
            await command
    assert not audio_device._audio_route_lock.locked()
    audio_device._request.assert_not_called()


@pytest.mark.parametrize("value", ["Input1", "NotAMode", ""])
async def test_invalid_output_modes_cannot_write(audio_device, value):
    with pytest.raises(CrestronNVXError, match="Invalid audio output mode"):
        await audio_device.set_audio_output_mode(value)
    audio_device._request.assert_not_called()


async def test_transmitters_cannot_receive(audio_device):
    audio_device.device_mode = "Transmitter"
    with pytest.raises(CrestronNVXError):
        await audio_device.set_aes67_stream("Stream01", "123")
    audio_device._request.assert_not_called()


def test_output_mode_feedback_is_configured_not_active(device, audio_coordinator):
    audio_coordinator.data["device_specific"] = {
        "AudioSource": "SecondaryStreamAudio",
        "ActiveAudioSource": "PrimaryStreamAudio",
    }
    entity = CrestronNVXAudioOutputSelect(audio_coordinator, device)
    assert entity.current_option == "DM NAX (AES67) Audio"
    assert entity.extra_state_attributes == {"active_audio_source": "PrimaryStreamAudio"}
    audio_coordinator.data["device_specific"] = {}
    assert not entity.available
