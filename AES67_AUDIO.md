# NAX Audio to an NVX Decoder

Available in the opt-in **2.3.0b1 prerelease**. The DM-NVX-D30 receive and
discovery objects were inspected on firmware **7.1.5259.00090**. Automated
tests cover commands and feedback; audible, end-to-end testing of these new
controls has not yet been completed. Stable 2.2.1 remains available.

## What Each Control Does

| Control | Purpose |
| --- | --- |
| Stream Source | Selects NVX video, retaining the existing video-switching behavior. |
| Audio Source | Existing NVX-to-NVX audio routing using discovered NVX source IDs. This is not the NAX feed selector. |
| Audio Follows Video | Existing switch controlling whether secondary audio follows NVX video routing. Turn it off to select an independent AES67 feed. |
| AES67 Stream | New selector for a feed discovered by the decoder, including compatible NAX transmit streams. Off stops and disables this receive slot only. |
| Audio Output Mode | Chooses which audio reaches the decoder output: Audio Follows Video, Primary Stream Audio, or DM NAX (AES67) Audio. It does not select a transmit stream. |

The **Audio Follows Video switch** and the similarly named **Audio Output
Mode option** are different Crestron settings. The switch controls secondary
stream routing. The output-mode option lets the decoder choose audio based
on its video input. Selecting DM NAX audio is an explicit output choice.

## Setup

1. In HACS, open **Crestron DM NVX**, enable prerelease/beta versions, and
   download **2.3.0b1**. Restart Home Assistant. No YAML configuration is needed.
2. Open **Settings > Devices & services > Crestron DM NVX**, then the decoder
   device you want to use, such as your D30 Bedroom decoder.
3. Leave **Stream Source** on the video you want. Note your current audio
   settings so you can restore them after testing.
4. Turn the **Audio Follows Video** switch **off**. The integration never
   turns this off automatically. The AES67 selector is unavailable while
   this flag is on or cannot be read.
5. In **AES67 Stream**, choose the NAX feed you want. Look for your NAX's
   address, for example `[192.168.1.80]`, after its advertised stream name.
6. Set **Audio Output Mode** to **DM NAX (AES67) Audio**. The decoder should
   now output that feed with its existing video. Ensure the receiving TV or
   audio equipment is enabled and not muted.

The NAX integration is not required for this receive selector. Discovery comes
from the **NVX decoder's** SAP/SDP list. The integration does not enable NAX
transmission, change NAX zone sources, or change Crestron Home configuration.
Crestron Home can still overwrite routes when its own programming commands a
change; coordinate ownership of the decoder while testing.

## What the Names Mean

Names are the transmitter's advertised session names, followed by its source
IP when available. They are not friendly aliases imported from the NAX
integration. Multiple feeds from one NAX keep their individual session names;
duplicate labels get a discovery ID suffix.

- `MediaStream...` identifies a NAX internal media-player transmit feed. It is
  not an analog output or a named room, and discovery does not mean music is
  currently playing on that player.
- `RCA...`, `S/PDIF...`, and `TOSLINK...` are examples of physical-input feeds
  advertised by the NAX. Their presence depends on its transmit configuration.
- A room/zone output is not automatically offered just because that zone
  exists. Only compatible streams advertised to this decoder appear. Use the
  NAX web interface's transmit stream list to match a feed to its actual source.

For the NAX at `192.168.1.80`, `MediaStream19c4.42.68.3f.bc.16` was observed
at multicast `239.8.0.0:5004`. This is an example from the inspected system,
not a default to configure on every NAX. The selector uses current discovery,
not a hard-coded address or a guessed relationship between zone and stream numbers.

## Restoring Video Audio

For an NVX video stream's embedded audio, set **Audio Output Mode** to
**Primary Stream Audio**. For the decoder's automatic audio selection, use
**Audio Follows Video** instead. Restore the **Audio Follows Video switch**
to its previous state if you also want secondary stream routes to follow video.

While that switch is still off, **AES67 Stream > Off** stops the separate
AES67 receiver. This is optional when choosing primary audio, but avoids
receiving an unused feed. The existing **Stream Source > Off** clears NVX
video/audio/USB UID routes; it does **not** stop this direct AES67 receiver.
**Audio Source > Off** also does not stop direct AES67 reception.

## Automation Example

Replace the entity IDs with those shown on your decoder's entity pages, and
copy the stream option exactly from its dropdown. The following sequence does
not change the video source or the NAX:

```yaml
sequence:
  - action: switch.turn_off
    target:
      entity_id: switch.bedroom_audio_follows_video
  - action: select.select_option
    target:
      entity_id: select.bedroom_aes67_stream
    data:
      option: "MediaStream19c4.42.68.3f.bc.16 [192.168.1.80]"
  - action: select.select_option
    target:
      entity_id: select.bedroom_audio_output_mode
    data:
      option: "DM NAX (AES67) Audio"
```

## Limits and Troubleshooting

- This beta exposes direct routing only when the decoder reports **one**
  receive slot. It does not guess output mappings on multi-receiver hardware.
- Offered feeds must advertise unencrypted, two-channel, 48 kHz, 24-bit LPCM
  with a valid IPv4 multicast address, UDP port, and receiver-compatible name.
  Encryption settings are never changed automatically.
- If only **Off** appears, check whether the decoder's own web UI discovers
  the desired feed. Check NAX transmission, multicast/SAP reachability, and
  the compatible format before changing integration settings.
- A started stream at the expected multicast address/port confirms reception,
  not audible sound. Check Audio Output Mode, the source's playback, downstream
  mute/volume, and the decoder's reported active audio source if it is silent.
- The selector reports actual receive state, not requested session names.
  Connecting, failed, or ambiguously mapped streams show unknown. An accepted
  command without confirmed reception raises an error and refreshes state.
  Commands use short settle polls within a 15-second deadline once running.
- The D30 can receive NAX audio but cannot retransmit that received AES67 feed.
  This feature does not add MP2 playback control or require enabling MP2.

## Crestron References

- [D30 audio capabilities](https://docs.crestron.com/en-us/9496/Content/Topics/Overview/Features-D30.htm)
- [D30 settings and audio output choices](https://docs.crestron.com/en-us/9496/Content/Topics/Configuration/DM-NVX-(ED)30_and_E760/Settings-DM-NVX-ED30_and_E760.htm)
- [NaxAudio receive and discovery API](https://sdkcon78221.crestron.com/sdk/DM_NVX_REST_API/Content/Topics/Objects/NaxAudio.htm)
- [DeviceSpecific audio source API](https://sdkcon78221.crestron.com/sdk/DM_NVX_REST_API/Content/Topics/Objects/DeviceSpecific.htm)
