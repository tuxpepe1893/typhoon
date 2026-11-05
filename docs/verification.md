# Verification record

Date: 2026-10-11. Hardware target: Tornado SMART-22X SQ (WIFI) BLACK, AUX manufacture, October 2021. Host: Apple Silicon Mac, macOS 27.0.1.

## Verified software behavior

### Desktop background operation (2026-10-11 update)

- Installed the updated desktop app at its current `/Applications/Typhoon.app` location. Repaired the CLI symlink and existing Apple Home launch job, which still referenced the former user Applications directory.
- The desktop launch agent is registered with `RunAtLoad` and restart-on-unsuccessful-exit behavior. Its actual launch hid the window; closing the window left the same desktop process running.
- Forced termination of the managed desktop process was followed by an automatic launch with a new PID. A background status subprocess was observed under the restarted GUI process.
- An additional installation successfully refreshed both launch jobs. The Apple Home bridge retained pairing and reported available with fresh AC reads. No AC settings were written during these checks.
- Release build, bundle signature verification, and installer syntax/plist checks passed. Startup was exercised through launchd in the current login session; a full logout/reboot was not performed. System sleep is not inhibited. Explicit Quit is respected until reopening or next login.

### Initial application verification

- 148 automated Python tests pass. These use synthetic protocol frames, fake network responses, fake account/session providers, and mocked HomeKit lifecycle operations; they do not control hardware.
- SwiftUI debug and release builds pass. Isolated controller checks cover subprocess JSON, private login stdin, nullable cloud features, independent readback, account changes, and rejection of a different device identity.
- The frozen backend runs from the application bundle, including local diagnostics and the cloud/Keychain import path. It does not require the development virtual environment at runtime.
- The installed app at `~/Applications/Typhoon.app` launches. Its local discovery interface was exercised through macOS Accessibility, discovered the actual unit after pairing, and connected to it. The CLI is installed at `~/.local/bin/typhoon`.
- Ad hoc bundle signature verification passes before and after staged installation.
- Real macOS Keychain create/read/update/delete passed with a unique temporary **nonsecret** test item, which was removed.
- Both configured vendor cloud hostnames are reachable over verified HTTPS. This does not establish account login or device control.

## Network and pairing findings

The router serves separate 2.4 GHz and 5 GHz networks with an active internet connection. The Mac is on the router's local subnet. Standard Broadlink discovery, alternate header/address variants, targeted local probes, and passive AUX announcement listening did not identify the AC before pairing was repaired. The router's device inventory requires login; no router settings or credentials were changed.

The official Tornado video confirms HEALTH eight times followed by two beeps. Photographs of the supplied remote show HEALTH behind the flip-open cover, below SPEED and above SLEEP. After the user completed pairing in Tornado WiFi 3 on the 2.4 GHz network, discovery found one AUX air conditioner, device type `0x4E2A`, unlocked, using UDP port 80. Local authentication succeeded from the Mac on 5 GHz. No cloud account or additional IR hardware was required.

## Verified hardware behavior and display correction

The installed GUI discovered and selected the actual unit, then independently read its live settings. The installed CLI also authenticated and read power, mode, temperature, fan, swing, display data, and ambient temperature.

The first GUI display test produced a physical response: the user confirmed **“Display went dark; I changed it to 17°C.”** Subsequent attempts to restore the display reported `display: true`, but the user observed that the numbers remained dark. Those initial readbacks did **not** establish a successful restoration.

Follow-up physical testing identified inverted display polarity on this device type, `0x4E2A`: bit 4 of state byte 10 set to **1 means dark**, and **0 means visible** while the unit is powered on. The user confirmed that the numbers were visible after the corrected command. The Python controller now applies this model profile when reading and writing; other device types retain their existing mapping. Regression tests cover this inversion.

The latest installed Mac app/backend reports normalized `display: true` after the corrected display-on command, and the physical numbers were confirmed visible. Unchanged settings remain preserved; no temperature restoration is claimed from the initial, incorrectly interpreted display results.

Raw before/after JSON is retained only in the ignored `local/` directory. Device addresses and credentials are excluded from this record. Device readbacks and physical observations are recorded separately because a protocol flag alone did not initially establish visible behavior.

## HomeKit bridge

The installed Mac Home bridge has been enabled. Fresh installed CLI status reports `paired: true`, `available: true`, `running: true`, `enabled: true`, and `serviceLoaded: true`. Bonjour advertising for **Typhoon Home Bridge** on port **51826** was verified. The bridge uses the configured local AC, shared controller locking, pinned device identity, and fresh verified readbacks. Private pairing material remains in local Application Support.

The user confirmed successful pairing in Apple Home. The user then set the Home thermostat to **18°C** and confirmed that the physical AC display changed to **18°C**. This completes the Apple Home pairing and physical thermostat-control checks, in addition to the bridge's live availability checks. Python 3.14 startup compatibility and startup failure cleanup are covered by focused tests.

## Verification limits

### Home connectivity investigation (2026-10-11, afternoon)

The owner later reported Apple Home stuck on Updating/No Response. This later
failure remains distinct from the earlier successful physical control test.
The bridge still reads the AC, retains its original pairing identity and client,
listens on its advertised LAN address, and resolves through Bonjour on the Mac.
A browser request from another device on the LAN reached the same HomeKit TCP port and
received a HAP error response. That confirms a network path; it is not an
authenticated Apple Home control test or proof of repaired Home connectivity.

During diagnosis, replacing a launchd job reproduced an immediate bootstrap
failure that succeeded when retried. Service registration now retries transient
EIO briefly, with regression tests for recovery, preserved pairing, and immediate
failure on permission errors. Installation now also restarts an already-loaded
bridge so updated executable code takes effect. The focused HomeKit/service suite
passed **46 tests**. Timestamped connection metadata is logged without enabling
protocol payload/debug logging.

The installed Mac app and backend used valid **Developer ID**
signatures. The backend uses a stable signing identifier across rebuilds. This
build is not notarized. Signing and local service health alone have not resolved
the reported Apple Home issue. No AC firmware modification was performed; the
owner requires explicit approval before any such work.

### Remaining acceptance limits

Live physical tests covered indoor display control from the Mac, plus an Apple Home target-temperature change to 18°C. Power, HVAC mode, fan, swing, and every temperature value were not individually cycled on the physical unit; their encoding and readback behavior have automated coverage. No real Tornado cloud account has been signed in through Typhoon; the cloud fallback remains unverified against this user's account. The Mac app is not a notarized distribution release.
