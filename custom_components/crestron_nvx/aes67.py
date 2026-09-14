"""Conservative discovery and receive-state helpers for NVX AES67 audio."""

from __future__ import annotations

from collections import Counter
from ipaddress import ip_address

OFF = "Off"
AUDIO_OUTPUT_MODES = {
    "Audio Follows Video": "AudioFollowsVideo",
    "Primary Stream Audio": "PrimaryStreamAudio",
    "DM NAX (AES67) Audio": "SecondaryStreamAudio",
}


def valid_key(value: object) -> bool:
    return isinstance(value, str) and value.isascii() and value.isalnum()


def single_receiver(receivers: dict) -> str | None:
    """Do not guess output mappings on devices with multiple receive slots."""
    if len(receivers) == 1:
        key, value = next(iter(receivers.items()))
        if valid_key(key) and isinstance(value, dict) and "StreamStatus" in value:
            return key
    return None


def stream_endpoint(stream: dict) -> tuple[str, int] | None:
    address, port = stream.get("NetworkAddressStatus"), stream.get("PortStatus")
    if not isinstance(address, str) or type(port) is not int or not 1025 <= port <= 65535:
        return None
    try:
        parsed = ip_address(address)
    except ValueError:
        return None
    return (str(parsed), port) if parsed.version == 4 and parsed.is_multicast else None


def compatible_stream(stream: dict) -> bool:
    """Only offer the unencrypted stereo LPCM format supported by D30 receivers."""
    name = stream.get("SessionNameStatus")
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 31
        and bool(name.strip())
        and not name.startswith("-")
        and name.isprintable()
        and stream_endpoint(stream) is not None
        and stream.get("ChannelsNum") == 2
        and stream.get("EncodingSampleRate") == 48000
        and stream.get("EncodingFormat") == "L24"
        and stream.get("IsEncryptionEnabled") is False
    )


def stream_started(stream: dict) -> bool:
    return (
        stream.get("StreamStatus") == "Stream Started"
        and stream.get("IsDisabled") is False
        and stream.get("ErrCode") == "OK"
    )


def stream_options(streams: dict) -> dict[str, str | None]:
    names = {}
    for key, stream in streams.items():
        if not valid_key(key) or not isinstance(stream, dict) or not compatible_stream(stream):
            continue
        name = stream["SessionNameStatus"]
        try:
            source = ip_address(stream.get("SourceNetworkAddress", ""))
        except (TypeError, ValueError):
            pass
        else:
            name = f"{name} [{source}]"
        names[key] = name
    counts = Counter(names.values())
    options = {OFF: None}
    for key, name in sorted(names.items(), key=lambda item: (item[1], item[0])):
        label = f"{name} ({key})" if name == OFF or counts[name] > 1 else name
        while label in options:
            label += f" ({key})"
        options[label] = key
    return options
