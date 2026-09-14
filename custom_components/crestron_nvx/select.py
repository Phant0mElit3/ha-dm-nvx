"""Select platforms for Crestron NVX: network source, audio source, and HDMI input switching."""

from __future__ import annotations

from collections import Counter

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .aes67 import (
    AUDIO_OUTPUT_MODES,
    single_receiver,
    stream_endpoint,
    stream_options,
    stream_started,
)
from .const import DOMAIN
from .entity import async_run_command, crestron_device_info

OFF_OPTION = "Off"


def source_options(streams: dict, current_uid: str | None) -> dict[str, str]:
    """Keep duplicate/reserved source names selectable and current routes visible."""
    names = {
        uid: info.get("SessionName") or f"Unnamed ({uid})"
        for uid, info in streams.items()
        if isinstance(info, dict)
    }
    counts = Counter(names.values())
    options = {OFF_OPTION: ""}
    for uid, name in sorted(names.items(), key=lambda item: (item[1], item[0])):
        label = f"{name} ({uid})" if name == OFF_OPTION or counts[name] > 1 else name
        while label in options:
            label = f"{label} ({uid})"
        options[label] = uid
    if current_uid and current_uid not in options.values():
        label = f"Unknown ({current_uid})"
        while label in options:
            label += f" ({current_uid})"
        options[label] = current_uid
    return options


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Crestron NVX select entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators = data["coordinators"]
    api = data["api"]

    entities = []
    for device_name, coordinator in coordinators.items():
        device = api.get_device(device_name)
        if device.is_receiver:
            entities.append(CrestronNVXStreamSelect(coordinator, device))
            entities.append(CrestronNVXAudioSourceSelect(coordinator, device))
            if device.hdmi_inputs > 0:
                entities.append(CrestronNVXLocalSourceSelect(coordinator, device))
        elif device.is_transmitter and device.hdmi_inputs > 1:
            entities.append(CrestronNVXTransmitterInputSelect(coordinator, device))
        if device.test_patterns:
            entities.append(CrestronNVXTestPatternSelect(coordinator, device))

    async_add_entities(entities)

    added = set()

    @callback
    def add_audio_controls() -> None:
        """Allow optional capabilities to appear on a later successful poll."""
        new_entities = []
        for coordinator in coordinators.values():
            device = coordinator.device
            if not device.is_receiver:
                continue
            status = coordinator.data or {}
            receiver = single_receiver(status.get("aes67_receivers") or {})
            if receiver:
                key = (device.entity_id_prefix, "aes67")
                if key not in added:
                    added.add(key)
                    new_entities.append(CrestronNVXAes67Select(coordinator, device))
            mode = (status.get("device_specific") or {}).get("AudioSource")
            if receiver and mode in AUDIO_OUTPUT_MODES.values():
                key = (device.entity_id_prefix, "audio_output")
                if key not in added:
                    added.add(key)
                    new_entities.append(CrestronNVXAudioOutputSelect(coordinator, device))
        if new_entities:
            async_add_entities(new_entities)

    add_audio_controls()
    for coordinator in coordinators.values():
        entry.async_on_unload(coordinator.async_add_listener(add_audio_controls))


class CrestronNVXStreamSelect(CoordinatorEntity, SelectEntity):
    """Route primary video with receive readback; Off clears NVX UID routes."""

    def __init__(self, coordinator, device):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} Stream Source"
        self._attr_unique_id = f"{device.entity_id_prefix}_stream_source"
        self._attr_icon = "mdi:video-input-hdmi"
        self._attr_device_info = crestron_device_info(device)

    def _source_options(self) -> dict[str, str]:
        data = self.coordinator.data or {}
        route = data.get("route") or {}
        return source_options(data.get("discovered_streams") or {}, route.get(self._route_key))

    _route_key = "VideoSource"

    @property
    def options(self) -> list[str]:
        return list(self._source_options())

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        stream = data.get("primary_stream")
        if self._route_key == "VideoSource" and stream is not None:
            location = stream.get("StreamLocation")
            if stream.get("Processing") or not isinstance(location, str):
                return None
            if not location:
                return (
                    OFF_OPTION
                    if str(stream.get("Status", "")).casefold() == "stream stopped"
                    else None
                )
            if str(stream.get("Status", "")).casefold() != "stream started":
                return None
            matches = {
                uid
                for uid, info in (data.get("discovered_streams") or {}).items()
                if isinstance(info, dict) and info.get("RtspUri") == location
            }
            if len(matches) != 1:
                return None
            uid = matches.pop()
            return next(
                (label for label, source in self._source_options().items() if source == uid), None
            )
        route = (self.coordinator.data or {}).get("route")
        if not route or self._route_key not in route:
            return None
        uid = route[self._route_key]
        return next(
            (label for label, source in self._source_options().items() if source == uid), None
        )

    async def async_select_option(self, option: str) -> None:
        sources = self._source_options()
        if option not in sources:
            raise ServiceValidationError("The selected NVX source is no longer available")
        uid = sources[option]
        try:
            await async_run_command(
                self.coordinator, self.device.set_route(uid) if uid else self.device.set_route_off()
            )
        except HomeAssistantError:
            # A partially applied command must still reconcile with the receiver.
            await self.coordinator.async_request_refresh()
            raise


class CrestronNVXAudioSourceSelect(CrestronNVXStreamSelect):
    """Independent audio routing, available while audio-follow is disabled."""

    _route_key = "AudioSource"

    def __init__(self, coordinator, device):
        super().__init__(coordinator, device)
        self._attr_name = f"{device.name} Audio Source"
        self._attr_unique_id = f"{device.entity_id_prefix}_audio_source"
        self._attr_icon = "mdi:volume-high"

    @property
    def available(self) -> bool:
        control = (self.coordinator.data or {}).get("route_control") or {}
        return super().available and control.get("IsSecondaryAudioFollowsVideoEnabled") is False

    async def async_select_option(self, option: str) -> None:
        if not self.available:
            raise ServiceValidationError("Disable Audio Follows Video before routing audio")
        sources = self._source_options()
        if option not in sources:
            raise ServiceValidationError("The selected NVX source is no longer available")
        await async_run_command(self.coordinator, self.device.set_audio_source(sources[option]))


class CrestronNVXAes67Select(CoordinatorEntity, SelectEntity):
    """Direct AES67 reception, independent from the NVX video/UID routes."""

    def __init__(self, coordinator, device):
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} AES67 Stream"
        self._attr_unique_id = f"{device.entity_id_prefix}_aes67_stream"
        self._attr_icon = "mdi:audio-input-rca"
        self._attr_device_info = crestron_device_info(device)

    @property
    def _receiver_id(self) -> str | None:
        return single_receiver((self.coordinator.data or {}).get("aes67_receivers") or {})

    @property
    def _receiver(self) -> dict:
        return ((self.coordinator.data or {}).get("aes67_receivers") or {}).get(
            self._receiver_id, {}
        )

    def _source_options(self) -> dict[str, str | None]:
        return stream_options((self.coordinator.data or {}).get("aes67_streams") or {})

    @property
    def available(self) -> bool:
        control = (self.coordinator.data or {}).get("route_control") or {}
        return (
            super().available
            and self._receiver_id is not None
            and control.get("IsSecondaryAudioFollowsVideoEnabled") is False
        )

    @property
    def options(self) -> list[str]:
        return list(self._source_options())

    @property
    def current_option(self) -> str | None:
        receiver = self._receiver
        if receiver.get("StreamStatus") == "Stream Stopped":
            return OFF_OPTION
        if not stream_started(receiver) or stream_endpoint(receiver) is None:
            return None
        streams = (self.coordinator.data or {}).get("aes67_streams") or {}
        matches = [
            label
            for label, key in self._source_options().items()
            if key is not None and stream_endpoint(streams[key]) == stream_endpoint(receiver)
        ]
        return matches[0] if len(matches) == 1 else None

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "receiver_id": self._receiver_id,
            "stream_status": self._receiver.get("StreamStatus"),
            "stream_error": self._receiver.get("ErrCode"),
        }

    async def async_select_option(self, option: str) -> None:
        if not self.available:
            raise ServiceValidationError("Disable Audio Follows Video before routing AES67 audio")
        sources = self._source_options()
        if option not in sources:
            raise ServiceValidationError("The selected AES67 stream is no longer available")
        try:
            await async_run_command(
                self.coordinator, self.device.set_aes67_stream(self._receiver_id, sources[option])
            )
        except HomeAssistantError:
            await self.coordinator.async_request_refresh()
            raise


class CrestronNVXAudioOutputSelect(CoordinatorEntity, SelectEntity):
    """Select which audio reaches the decoder output, independently of video."""

    def __init__(self, coordinator, device):
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} Audio Output Mode"
        self._attr_unique_id = f"{device.entity_id_prefix}_audio_output_mode"
        self._attr_icon = "mdi:volume-high"
        self._attr_options = list(AUDIO_OUTPUT_MODES)
        self._attr_device_info = crestron_device_info(device)

    @property
    def available(self) -> bool:
        status = self.coordinator.data or {}
        return (
            super().available
            and single_receiver(status.get("aes67_receivers") or {}) is not None
            and self.current_option is not None
        )

    @property
    def current_option(self) -> str | None:
        specific = (self.coordinator.data or {}).get("device_specific") or {}
        return next(
            (
                label
                for label, value in AUDIO_OUTPUT_MODES.items()
                if value == specific.get("AudioSource")
            ),
            None,
        )

    @property
    def extra_state_attributes(self) -> dict:
        specific = (self.coordinator.data or {}).get("device_specific") or {}
        return {"active_audio_source": specific.get("ActiveAudioSource")}

    async def async_select_option(self, option: str) -> None:
        if not self.available or option not in AUDIO_OUTPUT_MODES:
            raise ServiceValidationError("The selected audio output mode is not available")
        try:
            await async_run_command(
                self.coordinator, self.device.set_audio_output_mode(AUDIO_OUTPUT_MODES[option])
            )
        except HomeAssistantError:
            await self.coordinator.async_request_refresh()
            raise


class CrestronNVXLocalSourceSelect(CoordinatorEntity, SelectEntity):
    """Local HDMI input vs. network Stream select, for receivers with a local HDMI input.

    Only created when the device reports at least one local HDMI input
    (DeviceCapabilities.PortConfig.NumberOfHdmiInputs > 0) - most receivers
    in this fleet don't have one at all. Backed by DeviceSpecific.VideoSource,
    confirmed live to accept "Stream" and "InputN" and to correctly resume
    the existing AvRouting route when switched back to "Stream".
    """

    def __init__(self, coordinator, device):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} Video Input"
        self._attr_unique_id = f"{device.entity_id_prefix}_video_input"
        self._attr_icon = "mdi:swap-horizontal"
        self._attr_options = ["Stream"] + [
            f"Local Input {i + 1}" for i in range(device.hdmi_inputs)
        ]
        self._attr_device_info = crestron_device_info(device)

    @property
    def current_option(self) -> str | None:
        """Return "Stream" or "Local Input N", from DeviceSpecific.VideoSource."""
        value = ((self.coordinator.data or {}).get("device_specific") or {}).get("VideoSource")
        if value == "Stream":
            return "Stream"
        if value and value.startswith("Input"):
            return f"Local Input {value[len('Input') :]}"
        return None

    async def async_select_option(self, option: str) -> None:
        """Switch between the network stream and a local HDMI input."""
        if option == "Stream":
            value = "Stream"
        else:
            index = option.removeprefix("Local Input ").strip()
            value = f"Input{index}"

        if option not in self.options:
            raise ServiceValidationError("Invalid NVX option")
        await async_run_command(self.coordinator, self.device.set_video_source(value))


class CrestronNVXTransmitterInputSelect(CoordinatorEntity, SelectEntity):
    """HDMI input select, for transmitters with more than one HDMI input.

    Only created when NumberOfHdmiInputs > 1 (e.g. a DM-NVX-352) - a
    single-input transmitter like a DM-NVX-E30 has nothing to switch
    between. Same DeviceSpecific.VideoSource field as the receiver-side
    local/stream select, just without the "Stream" option.
    """

    def __init__(self, coordinator, device):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} HDMI Input"
        self._attr_unique_id = f"{device.entity_id_prefix}_hdmi_input"
        self._attr_icon = "mdi:video-input-hdmi"
        self._attr_options = [f"Input {i + 1}" for i in range(device.hdmi_inputs)]
        self._attr_device_info = crestron_device_info(device)

    @property
    def current_option(self) -> str | None:
        """Return "Input N", from DeviceSpecific.VideoSource."""
        value = ((self.coordinator.data or {}).get("device_specific") or {}).get("VideoSource")
        if value and value.startswith("Input"):
            return f"Input {value[len('Input') :]}"
        return None

    async def async_select_option(self, option: str) -> None:
        """Switch the active HDMI input."""
        index = option.removeprefix("Input ").strip()
        if option not in self.options:
            raise ServiceValidationError("Invalid NVX option")
        await async_run_command(self.coordinator, self.device.set_video_source(f"Input{index}"))


class CrestronNVXTestPatternSelect(CoordinatorEntity, SelectEntity):
    """Test pattern generator on a transmitter's output.

    Only created when the device reports any TestPatternsSupported (a
    transmitter-only feature - confirmed live absent on receivers). Options
    are whatever the device itself lists (e.g. "SMPTE ColorBars", "Black",
    "Grid"), read once at login since the supported set is static.
    Overrides whatever's actually connected to the HDMI input - useful for
    verifying the downstream signal chain without a real source. "Off"
    restores the real source; confirmed live to apply immediately with no
    read-after-write lag.
    """

    def __init__(self, coordinator, device):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.name} Test Pattern"
        self._attr_unique_id = f"{device.entity_id_prefix}_test_pattern"
        self._attr_icon = "mdi:contrast-box"
        self._attr_options = device.test_patterns
        self._attr_device_info = crestron_device_info(device)

    @property
    def current_option(self) -> str | None:
        """Return the currently active test pattern."""
        return (self.coordinator.data or {}).get("test_pattern")

    async def async_select_option(self, option: str) -> None:
        """Set the active test pattern, or restore the real source with Off."""
        if option not in self.options:
            raise ServiceValidationError("Invalid NVX option")
        await async_run_command(self.coordinator, self.device.set_test_pattern(option))
