# Contributing to Typhoon

Thanks for your interest in Typhoon. Bug reports, protocol findings for other AUX-based units, and pull requests are welcome.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

You need macOS 14 or later, the Xcode command-line tools, and [`uv`](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev
uv run --extra dev pytest -q      # Python controller, CLI, cloud, and HomeKit tests
swift build                       # macOS SwiftUI front end
./scripts/build-app.sh            # full bundle in build/Typhoon.app
```

The test suite uses synthetic protocol frames and fakes for the network, cloud, Keychain, and launchd. It never contacts a real air conditioner or vendor account.

## Pull requests

- Keep changes focused, and add or update tests for behavior changes.
- Run `pytest` and `swift build` before opening a PR.
- Protocol changes need a packet fixture and a note on which device type (`devtype`) they were observed on. Don't generalize a model-specific quirk to every AUX unit.
- Writes must keep the existing guarantees: read current state first, preserve unrelated bits, and confirm with an independent readback before reporting success.

## Privacy

Never commit device MAC addresses, IP addresses, cloud credentials or session tokens, HomeKit pairing material, or raw captures from your network. Use documentation-range addresses (`192.0.2.0/24`) and placeholder MACs such as `aa:bb:cc:dd:ee:ff` in tests and examples. Keep local diagnostics in the ignored `local/` directory.

## Reporting bugs

Open a GitHub issue with your macOS version, the AC model and `devtype` (from `typhoon --json discover`), the command you ran, and the redacted output. For security issues, follow [SECURITY.md](SECURITY.md) instead.
