# Typhoon

A native macOS app and CLI for local Wi-Fi control of a Tornado SMART‑22X SQ (WIFI) air conditioner, with an Apple Home bridge hosted on the Mac. Local control uses the AUX/Broadlink protocol, and the app and CLI share a Python controller. An optional Tornado account connection provides a cloud fallback.

Typhoon has been tested against a real SMART‑22X SQ (AUX device type `0x4E2A`). The Mac app controlled the indoor display, and physical testing found and corrected this model's inverted display flag. Pairing with Apple Home also worked: setting the Home thermostat to 18°C changed the AC's own display to 18°C. Other AUX-based units may work but are untested. The cloud fallback has not been verified against a real account. See the [verification record](docs/verification.md) and [protocol research](docs/research.md).

> Typhoon is an independent project. It is not affiliated with or endorsed by Tornado, AUX, Broadlink, or Apple.

## Open the app

After [installation](#build-and-install):

```sh
open "$HOME/Applications/Typhoon.app"
typhoon --json doctor
```

In **Connection settings**, select **Local Wi‑Fi** and find the air conditioner. Keep your existing Tornado WiFi 3 pairing in place. The Mac and the AC must be on the same LAN. The Mac can use 5 GHz while the AC uses 2.4 GHz, as long as the router connects the two networks. Allow local network access if macOS asks. If discovery doesn't find the unit, you can enter a known IP and MAC manually.

If local control is unavailable, choose **Tornado account** and sign in with your Tornado WiFi 3 account. Start with the **USA** server for an Israeli account, as documented by the Tornado-specific integration linked in the research. The app keeps the resulting session and private device records in macOS Keychain. It doesn't keep your account password. Cloud use needs internet access and a working vendor service.

If the AC is offline, it first has to join Wi-Fi through Tornado WiFi 3, using a phone on the household 2.4 GHz network. Tornado's official X-series procedure is to press **HEALTH eight times** and wait for two beeps. Then choose **Add device** in the phone app and enter the Wi-Fi password. On the SMART‑22X remote, HEALTH is **behind the flip-open cover, below SPEED and above SLEEP**. Other remote models may need a different procedure. [Official demonstration](https://www.youtube.com/watch?v=UggjlfWnQ0k&t=90s).

## CLI

```sh
typhoon discover
typhoon configure --host AC_IP --mac AC_MAC --name "Living room"
typhoon status
typhoon set --temperature 24 --mode cool
typhoon set --display off
typhoon set --power off
```

Temperature accepts 16–32 °C in half-degree steps. Modes: `auto`, `cool`, `dry`, `heat`, `fan`. Fan speeds: `auto`, `low`, `medium`, `high`. `--swing` controls vertical swing. If a setting or feature isn't supported by the selected transport, the CLI rejects it or shows it as unavailable.

For cloud use:

```sh
typhoon cloud-login --email YOUR_EMAIL --region usa
# The terminal prompts privately for the password.
typhoon cloud-devices
typhoon cloud-select --id DEVICE_ID
typhoon status
typhoon cloud-logout
```

`--json` gives structured output. A failed command returns a nonzero exit status and an error. Every write reads the existing state first, keeps unrelated settings as they were, and confirms the result with a separate readback. `verified` means the AC reported the requested settings; it isn't the same as seeing the change on the unit. The GUI labels cloud readbacks separately. If a command times out after it was sent, refresh before retrying, because the unit may already have applied it.

## Apple Home

Connect to the local AC in Typhoon, then open **Apple Home** in the sidebar and select **Enable Bridge**. Once the bridge is available, select **Show Pairing Code**. On an iPhone or iPad, open Apple's Home app, choose **Add Accessory**, and scan the QR code or enter the displayed setup code. The bridge exposes a thermostat and a separate switch for the indoor display.

For Home control to work, keep this Mac awake, signed in, and connected to the AC's LAN. Enabling the bridge installs a login service, so it starts again whenever you sign in. Disabling the bridge stops that service but keeps the pairing information.

The same operations are available from the CLI:

```sh
typhoon homekit enable
typhoon --json homekit status
typhoon homekit pairing
# When needed:
typhoon homekit disable
```

`homekit pairing` prints the local setup code and URI. Ordinary status output leaves them out. Pairing keys are kept in `~/Library/Application Support/Typhoon/homekit/` with owner-only permissions. The bridge controls the configured local AC and doesn't need a Tornado cloud account.

## Build and install

You need macOS 14 or later, the Xcode command-line tools, and [`uv`](https://docs.astral.sh/uv/). The app bundle includes its own Python runtime and dependencies, so it doesn't use the project's virtual environment once installed.

```sh
./scripts/build-app.sh
./scripts/install.sh
uv run --extra dev pytest -q
```

The build goes to `build/Typhoon.app`. Installation puts the app in `~/Applications/Typhoon.app` (or replaces an existing `/Applications/Typhoon.app` if you moved it there) and links the CLI at `~/.local/bin/typhoon`. If the Apple Home service is already enabled, installation refreshes and restarts it so it runs the newly installed code. Builds are signed ad hoc by default. To sign the app with a stable identity for its network backend across updates, set `TYPHOON_SIGNING_IDENTITY` to an installed Developer ID Application identity before building. Signing doesn't notarize the app.

Installation also turns on the per-user **Typhoon desktop** login service. It starts the app hidden at login and keeps the menu-bar controls and live status available after you close the window. If the app crashes, the service restarts it. Choosing **Quit Typhoon** stops the app until you reopen it or log in again. The Apple Home bridge is managed separately and keeps running.

Manage automatic startup with `python3 scripts/background.py status`, `enable`, or `disable`. Disabling stops the managed desktop app and removes its login job, but leaves the Home pairing as it is. The app and the bridge only work while the Mac is awake and on the AC's network. This setting doesn't prevent the Mac from sleeping, and nothing runs before you log in.

Device settings, which aren't secret, are stored in `~/Library/Application Support/Typhoon/` with owner-only file permissions. Account sessions use the Keychain service `com.tuxpepe1893.typhoon`. Router passwords are never stored.

## Protocol

The controller uses `python-broadlink` for the outer UDP authentication and encryption transport. Typhoon handles the inner AUX packets itself, because the pinned library's HVAC code doesn't match the independently documented AUX checksum, mode, and temperature encoding. Tests include known packet fixtures and check that unrelated raw state bits survive a write.

Device type `0x4E2A` (the physically tested model) inverts the indoor-display flag: a wire bit of 1 means the display is dark, and 0 means it's visible while the unit is on. Typhoon normalizes this so that the public `display: true` always means visible. This correction applies only to `0x4E2A`. It isn't applied to other device types or assumed for cloud commands.

[docs/research.md](docs/research.md) lists the research and upstream sources.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) to get started, and [SECURITY.md](SECURITY.md) to report vulnerabilities. Everyone participating in this project is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Typhoon is released under the [MIT License](LICENSE). Notices for bundled dependencies are copied into `Typhoon.app/Contents/Resources/Licenses` at build time.
