# Tornado SMART-22X SQ Wi-Fi control research

Researched 2026-10-11. This document records source evidence and implementation choices. Successful local control of the actual unit is recorded in [verification.md](verification.md).

## Identification and existing setup

The supplied equipment label identifies **Tornado SMART-22X SQ (WIFI) BLACK**, manufactured by Ningbo AUX in October 2021. The user reports that this unit already works in **Tornado WIFI 3** on the household 2.4 GHz network.

Tornado's [product listing](https://www.tornado-top.co.il/catalog/product_page?productid=299) confirms Wi-Fi functionality for SMART-22X SQ. The manufacturer's [2022 catalog](https://www.tornado-top.co.il/download/files/%D7%A7%D7%98%D7%9C%D7%95%D7%92_2022.pdf) lists the black variant. The developer's [Google Play listing](https://play.google.com/store/apps/details?id=com.tornado.tornadowifi3&hl=en) says Tornado WIFI 3 supports X-series models from 2019 onward.

The official app's privacy-policy link is hosted on Broadlink infrastructure and includes the AC Freedom policy name:

https://app-service-chn-31a93883.ibroadlink.com/appfront/v1/webui/app-h5-privacy_policy_account_acfreedom-1353/?app=com.tornado.tornadowifi3&language=zh-cn&nightMode=false

This is direct evidence for the Broadlink/AC Freedom ecosystem. The Tornado-specific cloud integration cited below supplies independent evidence for account compatibility.

No reset or provisioning is needed while the existing connection works. If it is offline, Tornado's [official Wi-Fi setup video on its support page](https://www.tornado-top.co.il/page?pageid=240), embedded as [YouTube video UggjlfWnQ0k](https://www.youtube.com/watch?v=UggjlfWnQ0k), gives these steps for the X series:

1. Connect the phone to the household **2.4 GHz** Wi-Fi network.
2. In Tornado WIFI 3, choose **Add device** and grant the requested access permissions.
3. Press the remote's **HEALTH button eight times**, until the indoor unit emits a **double beep**. The remote shown in the video has a sliding cover exposing this button. The spoken instructions do not specify a long press.
4. Enter the household Wi-Fi password in the official app and choose **Start configuration**.
5. Allow up to **two minutes**. If configuration fails, the video says to repeat the connection process.

The exact HEALTH sequence is at [1:30–1:37](https://www.youtube.com/watch?v=UggjlfWnQ0k&t=90s). Verified from the manufacturer's actual video frames and a local Hebrew transcription of its audio, rather than a third-party summary. The official procedure uses a phone; a generic Mac `broadlink.setup()` is documented upstream only for an RM3 Mini and is not verified provisioning support for this AC. It should not be presented as a guaranteed substitute.

## Preferred route: direct local Broadlink control

The established [python-broadlink HVAC implementation](https://github.com/mjg59/python-broadlink/blob/master/broadlink/climate.py) explicitly lists **Tornado SMART X SQ series** as supported. [Upstream PR #520](https://github.com/mjg59/python-broadlink/pull/520), merged in April 2024, added Tornado 16X SQ support for device type **0x4E2A**. The pictured 22X belongs to the same family, but its actual discovery reply remains the authority for its device type.

The implementation provides local authentication, `get_state()`, `get_ac_info()`, and `set_state()`. State covers power, target temperature, HVAC mode, fan speed, quiet/turbo presets, horizontal and vertical swing, sleep, iFeel, display, health, cleaning, and mildew settings. Writes require a complete state, so the application should read the current state, modify requested fields, send it, and independently read it back. A transport acknowledgement alone is insufficient evidence of physical control.

The library uses Broadlink UDP communication, conventionally on port 80. Same-subnet connectivity matters; the Mac does not need to use the same Wi-Fi frequency if the router bridges its Ethernet/5 GHz connection to the AC's 2.4 GHz network. Guest isolation, separate VLANs, and VPN routing can prevent discovery or direct communication.

Some firmware advertises a locked device and rejects local authentication despite working through the manufacturer's app. The upstream PR contains a firsthand report of this behavior. Avoid deleting or unbinding a working unit merely because the first local authentication fails. Capture the discovery identity, lock status, and specific error before choosing a recovery route.

For a silent device, compare discovery packets before concluding it is locked. [Original AC Freedom traffic captured in issue #76](https://github.com/liaan/broadlink_ac_mqtt/issues/76) includes the magic header `5aa5aa555aa5aa55`, reversed local IPv4 bytes at offset `0x18`, an ephemeral UDP return port at `0x1C`, and discovery command `0x06` at `0x26` in a 48-byte packet. The [current python-broadlink scan](https://github.com/mjg59/python-broadlink/blob/master/broadlink/device.py) has the reversed address and return port but leaves the magic bytes zero. Testing a recomputed-checksum variant with the magic header is a bounded compatibility check. In the cited report, a locked unit still answered discovery and subsequently rejected authentication; total silence alone does not establish a lock.

## Newer integrations inspected

- [azadaydinli/ac_freedom](https://github.com/azadaydinli/ac_freedom) provides Home Assistant local Broadlink and AUX cloud paths. Its [local implementation](https://github.com/azadaydinli/ac_freedom/blob/main/custom_components/ac_freedom/broadlink_ac_api.py) broadcasts discovery on UDP 80, 15001, and 2415, and handles several response sizes. Its README describes newer BL1206-P modules as requiring the cloud path.
- [azadaydinli/homebridge-ac-freedom](https://github.com/azadaydinli/homebridge-ac-freedom) lists Tornado among compatible AC Freedom brands. Its [Node local client](https://github.com/azadaydinli/homebridge-ac-freedom/blob/main/src/broadlink-api.js) uses Node's built-in UDP and AES support; its [cloud client](https://github.com/azadaydinli/homebridge-ac-freedom/blob/main/src/cloud-api.js) implements SmartHomeCS login, device inventory, and parameter control. The local client hardcodes packet device type 0x272A and has weaker response validation than the established library, so it is useful protocol research rather than an automatically trusted replacement.
- [liaan/broadlink_ac_mqtt](https://github.com/liaan/broadlink_ac_mqtt) is older independent evidence for direct Broadlink AC control and specifically lists a tested Tornado TOP WiFi 12X.

## Fallback routes

**Cloud control:** [romfreiman/tornado-aircon-custom-component](https://github.com/romfreiman/tornado-aircon-custom-component) specifically supports Tornado accounts and instructs Israeli users to choose the **USA region**, which its README reports as verified. Its [API client](https://github.com/romfreiman/tornado-aircon-custom-component/blob/main/custom_components/tornado/aux_cloud/__init__.py) uses the same public AUX application identifiers: license ID `3c015b249dd66ef0f11f9bef59ecd737`, company ID `48eb1b36cf0202ab2ef07b880ecda60d`, and USA endpoint `https://app-service-usa-fd7cc04c.smarthomecs.com`. This gives a concrete fallback using the user's existing Tornado app email/password, without re-pairing an online unit. This user's actual cloud account still needs a live test.

[maeek/ha-aux-cloud](https://github.com/maeek/ha-aux-cloud) supplies a more extensive AUX/Broadlink cloud implementation with login/bootstrap over HTTPS and state updates over WebSocket. [ElDavoo/auxfreedom](https://github.com/ElDavoo/auxfreedom) supplies a standalone Python cloud client and CLI. Credentials belong in macOS Keychain or private runtime configuration, never the repository.

**Different AUX Wi-Fi generation:** ElDavoo's [AP provisioning research](https://github.com/ElDavoo/auxfreedom/blob/main/docs/ap-provisioning.md) describes newer modules using `a5 a5` frames, UDP 12414 provisioning, a TCP/UDP 12416 channel, and UDP 2415 announcements. It explicitly distinguishes these from classic Broadlink UDP/80. A device that fails classic discovery may therefore need another transport. This is a fallback lead, not proof that the photographed 2021 unit uses that protocol.

That source documents the frame wrapper but does not publish a complete read-only discovery request for port 12416. Its [timing investigation](https://github.com/ElDavoo/auxfreedom/blob/main/docs/pairing-timing-bug.md) reports unsolicited UDP 2415 announcements for roughly 32 seconds after joining Wi-Fi, followed by silence. Passive listening during an ordinary reconnection may therefore reveal identity without sending guessed provisioning messages. Firmware generation and timing must be distinguished from an incorrect subnet or client isolation.

**Infrared bridge:** A network-connected IR transmitter positioned in sight of the indoor receiver could emulate the existing remote if both embedded Wi-Fi routes fail. This requires additional hardware. One-way IR transmission does not prove the AC accepted a command; physical observation or an independent sensor would still be necessary. It is a contingency, since the user already has functioning built-in Wi-Fi.

## Apple Home without a Mac (research, 2026-10-11)

The owner explicitly requires approval before any AC firmware modification.
No firmware has been flashed or changed. The current solution translates the
stock module's local Broadlink/AUX protocol into HomeKit on the Mac. I have not
found verified native HomeKit support for the stock SMART-22X SQ Wi-Fi module.

Options that remove the Mac dependency:

- **External HomeKit infrared controller:** [Sensibo Air](https://support.sensibo.com/products/air/)
  documents native HomeKit support and remote-controlled AC compatibility. It
  leaves the AC hardware and firmware untouched, but adds a powered device and
  needs setup/remote-code compatibility confirmation. This is not the AC itself
  speaking HomeKit, and an IR command is not the independent AC readback that
  Typhoon currently performs.
- **Replacement internal Wi-Fi module:** the [AUX UART project](https://github.com/GrKoR/esphome_aux_ac_component)
  demonstrates direct control and feedback using an ESP module. Combining this
  with [HomeSpan](https://github.com/HomeSpan/HomeSpan) could make a replacement
  module speak HomeKit directly, without a Mac or Home Assistant. This is an
  engineering proposal, not a tested drop-in solution for this unit. The existing
  ESPHome component alone does not provide native HomeKit. Module identification,
  electrical compatibility, implementation and separate owner approval are
  required before any hardware or firmware work. The [hardware notes](https://github.com/GrKoR/esphome_aux_ac_component/blob/master/docs/HARDWARE-EN.md)
  describe multiple connectors and potentially non-USB voltages on USB-shaped
  sockets; a connector's shape does not establish compatibility.
- **External ESP32 Wi-Fi translator:** port Typhoon's verified local protocol to
  an ESP32 with HomeSpan. This would retain stock AC firmware and two-way status,
  and remove the Mac dependency, but still requires new hardware and unimplemented
  embedded software. It is a separate bridge, not native support in the AC.

None of these alternatives has been installed or tested on this unit. Merely
advertising a HomeKit Bonjour service cannot translate its control protocol.

## Live acceptance gate

1. Discover or identify the actual unit on the user's network and authenticate without disturbing existing pairing.
2. Read its current settings and room temperature through the app's production control path.
3. Make one controlled, reversible change from the Mac app and obtain a fresh device readback.
4. Have the user confirm the real indoor unit responds, then restore the prior setting if requested.
5. Exercise the installed GUI and CLI against the same physical device; document network/authentication limitations honestly.

Source inspection, a successful build, simulated responses, and a populated GUI are not substitutes for this live gate.
