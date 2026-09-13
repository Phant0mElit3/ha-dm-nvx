"""Switch platform for Crestron NVX - Audio Follows Video toggle (receivers)."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import async_run_command, crestron_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Crestron NVX switch entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators = data["coordinators"]
    api = data["api"]

    entities = []
    for device_name, coordinator in coordinators.items():
        device = api.get_device(device_name)
        if device.identify_supported:
            entities.append(CrestronNVXIdentifySwitch(coordinator, device))
        if device.is_receiver:
            status = coordinator.data or {}
            if isinstance(
                (status.get("route_control") or {}).get("IsSecondaryAudioFollowsVideoEnabled"), bool
            ):
                entities.append(CrestronNVXAudioFollowsVideoSwitch(coordinator, device))
            if isinstance((status.get("video") or {}).get("output_disabled"), bool):
                entities.append(CrestronNVXHdmiOutputSwitch(coordinator, device))
    async_add_entities(entities)


class CrestronNVXAudioFollowsVideoSwitch(CoordinatorEntity, SwitchEntity):
    """Toggle for AvRouting/RouteControl.IsSecondaryAudioFollowsVideoEnabled.

    When on (the device's own default), switching the video source also
    switches audio automatically - the "Audio Source" select entity is
    greyed out (unavailable) while this is on, since the device owns
    AudioSource in that state. When off, "Audio Source" becomes usable to
    route audio independently of video.
    """

    def __init__(self, coordinator, device):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} Audio Follows Video"
        self._attr_unique_id = f"{device.entity_id_prefix}_audio_follows_video"
        self._attr_icon = "mdi:link-variant"
        self._attr_device_info = crestron_device_info(device)

    @property
    def is_on(self) -> bool | None:
        """Return whether audio currently follows video."""
        route_control = (self.coordinator.data or {}).get("route_control") or {}
        return route_control.get("IsSecondaryAudioFollowsVideoEnabled")

    async def async_turn_on(self, **kwargs) -> None:
        """Enable audio-follows-video and immediately re-sync audio to the current video source."""
        await async_run_command(self.coordinator, self.device.set_audio_follows_video(True))

    async def async_turn_off(self, **kwargs) -> None:
        """Disable audio-follows-video, freeing the Audio Source select for independent use."""
        await async_run_command(self.coordinator, self.device.set_audio_follows_video(False))


class CrestronNVXIdentifySwitch(CoordinatorEntity, SwitchEntity):
    """Toggle the device's physical identify LED flashing mode."""

    def __init__(self, coordinator, device):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} Identify"
        self._attr_unique_id = f"{device.entity_id_prefix}_identify"
        self._attr_icon = "mdi:led-on"
        self._attr_device_info = crestron_device_info(device)

    @property
    def is_on(self) -> bool | None:
        """Return whether identify mode is active."""
        return (self.coordinator.data or {}).get("identify")

    async def async_turn_on(self, **kwargs) -> None:
        """Enable identify mode."""
        await async_run_command(self.coordinator, self.device.set_identify(True))

    async def async_turn_off(self, **kwargs) -> None:
        """Disable identify mode."""
        await async_run_command(self.coordinator, self.device.set_identify(False))


class CrestronNVXHdmiOutputSwitch(CoordinatorEntity, SwitchEntity):
    """Force-enable/disable a receiver's physical HDMI output.

    Independent of AvRouting: this blanks the output itself (confirmed live
    - the display goes to no-signal) rather than clearing the routed source,
    so turning it back on resumes whatever was already routed. Confirmed
    live that the device takes a couple of seconds to actually apply this -
    same read-after-write lag as Osd - so don't expect the state to flip on
    the very next poll immediately after toggling.
    """

    def __init__(self, coordinator, device):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} HDMI Output"
        self._attr_unique_id = f"{device.entity_id_prefix}_hdmi_output_enabled"
        self._attr_icon = "mdi:video-input-hdmi"
        self._attr_device_info = crestron_device_info(device)

    @property
    def is_on(self) -> bool:
        """Return True when the HDMI output is enabled (not force-disabled)."""
        video = (self.coordinator.data or {}).get("video") or {}
        disabled = video.get("output_disabled")
        return None if disabled is None else not disabled

    async def async_turn_on(self, **kwargs) -> None:
        """Re-enable the HDMI output."""
        await async_run_command(self.coordinator, self.device.set_output_disabled(False))

    async def async_turn_off(self, **kwargs) -> None:
        """Force-disable (blank) the HDMI output."""
        await async_run_command(self.coordinator, self.device.set_output_disabled(True))
