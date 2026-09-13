"""Home Assistant behavior: failures, entity identity, flows and commands."""

import asyncio
import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.crestron_nvx import (
    CrestronNVXDataUpdateCoordinator,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.crestron_nvx.config_flow import (
    CrestronNVXConfigFlow,
    CrestronNVXOptionsFlow,
    normalize_host,
)
from custom_components.crestron_nvx.crestron_nvx_api import (
    CrestronNVXAuthError,
    CrestronNVXConnectionError,
)
from custom_components.crestron_nvx.diagnostics import async_get_config_entry_diagnostics
from custom_components.crestron_nvx.entity import crestron_device_info
from custom_components.crestron_nvx.event import CrestronNVXCecEvent
from custom_components.crestron_nvx.notify import CrestronNVXOsdNotify
from custom_components.crestron_nvx.select import (
    CrestronNVXAudioSourceSelect,
    CrestronNVXStreamSelect,
    source_options,
)
from custom_components.crestron_nvx.switch import CrestronNVXHdmiOutputSwitch


def test_all_platforms_import():
    for platform in (
        "sensor",
        "binary_sensor",
        "select",
        "switch",
        "event",
        "notify",
        "number",
        "camera",
        "diagnostics",
    ):
        importlib.import_module(f"custom_components.crestron_nvx.{platform}")


@pytest.mark.parametrize(
    "host, expected",
    [("  NVX.LOCAL ", "nvx.local"), ("192.0.2.2", "192.0.2.2"), ("2001:db8::1", "[2001:db8::1]")],
)
def test_normalize_host(host, expected):
    assert normalize_host(host) == expected


@pytest.mark.parametrize(
    "host",
    ["", "https://nvx.local", "user:pass@nvx.local", "nvx.local/path", "nvx.local:443", "a b"],
)
def test_reject_urls_and_credentials(host):
    with pytest.raises(ValueError):
        normalize_host(host)


@pytest.mark.parametrize(
    "error, expected",
    [(CrestronNVXConnectionError(), UpdateFailed), (CrestronNVXAuthError(), ConfigEntryAuthFailed)],
)
async def test_coordinator_failure_classification(hass, device, error, expected):
    device.get_video_status = AsyncMock(side_effect=error)
    coordinator = CrestronNVXDataUpdateCoordinator(hass, device, timedelta(seconds=30))
    with pytest.raises(expected):
        await coordinator._async_update_data()


async def test_missing_status_does_not_report_connected(hass, device):
    device.get_video_status = AsyncMock(return_value=None)
    device.get_ethernet_status = AsyncMock(return_value={})
    coordinator = CrestronNVXDataUpdateCoordinator(hass, device, timedelta(seconds=30))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


def test_sources_with_duplicate_reserved_and_missing_names():
    options = source_options(
        {
            "a": {"SessionName": "TV"},
            "b": {"SessionName": "TV"},
            "c": {"SessionName": "Off"},
            "d": {},
        },
        "missing",
    )
    assert options == {
        "Off": "",
        "Off (c)": "c",
        "TV (a)": "a",
        "TV (b)": "b",
        "Unnamed (d)": "d",
        "Unknown (missing)": "missing",
    }


async def test_audio_off_leaves_video_untouched(device, coordinator):
    coordinator.data = {
        "route_control": {"IsSecondaryAudioFollowsVideoEnabled": False},
        "route": {"AudioSource": "", "VideoSource": "a"},
    }
    device.set_audio_source = AsyncMock(return_value=True)
    device.set_route_off = AsyncMock()
    entity = CrestronNVXAudioSourceSelect(coordinator, device)
    assert entity.current_option == "Off"
    await entity.async_select_option("Off")
    device.set_audio_source.assert_awaited_once_with("")
    device.set_route_off.assert_not_called()
    coordinator.data["route_control"]["IsSecondaryAudioFollowsVideoEnabled"] = True
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("Off")


async def test_rejected_command_raises(device, coordinator):
    device.set_route_off = AsyncMock(return_value=False)
    with pytest.raises(HomeAssistantError):
        await CrestronNVXStreamSelect(coordinator, device).async_select_option("Off")
    coordinator.async_request_refresh.assert_not_called()


def test_host_change_preserves_entity_and_device_identity(device, coordinator):
    before = CrestronNVXStreamSelect(coordinator, device).unique_id
    info = crestron_device_info(device)
    device.host = "192.0.2.20"
    assert CrestronNVXStreamSelect(coordinator, device).unique_id == before
    assert crestron_device_info(device)["identifiers"] == info["identifiers"]
    assert crestron_device_info(device)["configuration_url"] == "https://192.0.2.20"


def test_output_unknown_is_not_enabled(device, coordinator):
    assert CrestronNVXHdmiOutputSwitch(coordinator, device).is_on is None


async def test_cec_errors_back_off_and_task_can_be_cancelled(device, coordinator):
    device.longpoll = AsyncMock(side_effect=CrestronNVXConnectionError())
    listener = CrestronNVXCecEvent(coordinator, device)
    sleeping = asyncio.Event()

    async def sleep(_delay):
        sleeping.set()
        await asyncio.Event().wait()

    with patch("custom_components.crestron_nvx.event.asyncio.sleep", side_effect=sleep):
        listener._task = asyncio.create_task(listener._listen())
        await sleeping.wait()
        await listener.async_will_remove_from_hass()
    assert listener._task is None
    device.longpoll.assert_awaited_once()


async def test_replacing_osd_message_resets_clear_timer(hass, device, coordinator):
    entity = CrestronNVXOsdNotify(coordinator, device)
    entity.hass = hass
    device.set_osd = AsyncMock(return_value=True)
    await entity.async_send_message("first")
    first_task = entity._clear_task
    await entity.async_send_message("second")
    assert first_task.cancelled()
    assert entity._clear_task is not first_task
    await entity.async_will_remove_from_hass()
    assert entity._clear_task is None


@pytest.mark.parametrize(
    "error, expected",
    [
        (CrestronNVXAuthError(), ConfigEntryAuthFailed),
        (CrestronNVXConnectionError(), ConfigEntryNotReady),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
async def test_setup_failure_closes_session(hass, error, expected):
    entry = MagicMock(
        data={
            "devices": [
                {"host": "192.0.2.10", "name": "Display", "username": "admin", "password": "secret"}
            ]
        }
    )
    session = MagicMock(close=AsyncMock())
    api = MagicMock(add_device=AsyncMock(side_effect=error), close=AsyncMock())
    with (
        patch("custom_components.crestron_nvx.async_create_clientsession", return_value=session),
        patch("custom_components.crestron_nvx.CrestronNVXAPI", return_value=api),
    ):
        with pytest.raises(expected):
            await async_setup_entry(hass, entry)
    session.close.assert_awaited_once()
    api.close.assert_awaited_once()


async def test_unload_keeps_session_if_platform_refuses(hass):
    entry = MagicMock(entry_id="entry")
    session, api = MagicMock(close=AsyncMock()), MagicMock(close=AsyncMock())
    hass.data["crestron_nvx"] = {"entry": {"api": api, "session": session}}
    hass.config_entries = MagicMock(async_unload_platforms=AsyncMock(return_value=False))
    assert not await async_unload_entry(hass, entry)
    session.close.assert_not_awaited()
    hass.config_entries.async_unload_platforms.return_value = True
    assert await async_unload_entry(hass, entry)
    session.close.assert_awaited_once()


async def test_diagnostics_redact_identity_and_credentials(hass, device, coordinator):
    entry = MagicMock(entry_id="entry")
    entry.as_dict.return_value = {
        "data": {"devices": [{"host": device.host, "password": "secret", "username": "admin"}]}
    }
    hass.data["crestron_nvx"] = {
        "entry": {
            "api": SimpleNamespace(devices={"Display": device}),
            "coordinators": {"Display": coordinator},
        }
    }
    result = str(await async_get_config_entry_diagnostics(hass, entry))
    for sensitive in ("secret", "admin", device.host, "SERIAL123", "Display"):
        assert sensitive not in result


async def test_duplicate_config_aborts_instead_of_unknown_error(hass):
    flow = CrestronNVXConfigFlow()
    flow.hass = hass
    flow._validate = AsyncMock(
        return_value=({"host": "192.0.2.10", "serial_number": "SERIAL123"}, None)
    )
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock(side_effect=AbortFlow("already_configured"))
    with pytest.raises(AbortFlow) as exc:
        await flow.async_step_user({})
    assert exc.value.reason == "already_configured"


async def test_reconfigure_preserves_prefix_and_options(hass):
    previous = {"host": "192.0.2.10", "name": "Display", "serial_number": "SERIAL123"}
    entry = MagicMock(
        data={"devices": [previous]}, options={"enable_preview_camera": True}, unique_id="SERIAL123"
    )
    hass.config_entries = MagicMock(async_get_entry=MagicMock(return_value=entry))
    flow = CrestronNVXConfigFlow()
    flow.hass, flow.context = hass, {"entry_id": "entry"}
    flow._validate = AsyncMock(
        return_value=({**previous, "host": "192.0.2.20", "scan_interval": 20}, None)
    )
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
    await flow.async_step_reconfigure({})
    saved = flow.async_update_reload_and_abort.call_args.kwargs
    assert saved["data"]["devices"][0]["entity_id_prefix"] == "192.0.2.10"
    assert saved["options"] == {"enable_preview_camera": True, "scan_interval": 20}
    flow._validate.return_value = ({**previous, "serial_number": "OTHER"}, None)
    assert (await flow.async_step_reconfigure({}))["reason"] == "wrong_device"


async def test_options_uses_home_assistant_config_entry_property(hass):
    flow = CrestronNVXOptionsFlow()
    flow.hass = hass
    flow.handler = "entry"
    entry = MagicMock(options={"scan_interval": 40}, data={})
    hass.config_entries = MagicMock(async_get_known_entry=MagicMock(return_value=entry))
    result = await flow.async_step_init()
    assert result["data_schema"]({}) == {"enable_preview_camera": False, "scan_interval": 40}
