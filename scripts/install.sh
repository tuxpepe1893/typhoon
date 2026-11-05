#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -d build/Typhoon.app || ./scripts/build-app.sh
mkdir -p "$HOME/Applications" "$HOME/.local/bin"
target="$HOME/Applications/Typhoon.app"
if [ ! -e "$target" ] && [ -d /Applications/Typhoon.app ]; then
  target=/Applications/Typhoon.app
fi
if [ -e "$target" ]; then
  bundle_id=$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$target/Contents/Info.plist" 2>/dev/null || true)
  if [ "$bundle_id" != "com.tuxpepe1893.typhoon" ]; then
    echo "Refusing to replace an unrelated application at $target" >&2
    exit 1
  fi
fi
if [ -e "$HOME/.local/bin/typhoon" ] && [ ! -L "$HOME/.local/bin/typhoon" ]; then
  echo "Refusing to replace an existing nonsymlink CLI at $HOME/.local/bin/typhoon" >&2
  exit 1
fi
staging=$(mktemp -d "$(dirname "$target")/.typhoon-install.XXXXXX")
cleanup() {
  install_status=$?
  if [ "$install_status" -ne 0 ] && [ -d "$staging/previous.app" ]; then
    if [ -e "$target" ]; then mv "$target" "$staging/failed.app"; fi
    mv "$staging/previous.app" "$target"
  fi
  rm -rf "$staging"
}
trap cleanup EXIT
ditto build/Typhoon.app "$staging/Typhoon.app"
codesign --verify --deep --strict "$staging/Typhoon.app"
"$staging/Typhoon.app/Contents/Resources/typhoon-backend" --json --version >/dev/null
if [ -e "$target" ]; then mv "$target" "$staging/previous.app"; fi
mv "$staging/Typhoon.app" "$target"
codesign --verify --deep --strict "$target"
ln -sfn "$target/Contents/Resources/typhoon-backend" "$HOME/.local/bin/typhoon"
python3 scripts/background.py enable
if [ -f "$HOME/Library/LaunchAgents/com.tuxpepe1893.typhoon.homekit.plist" ]; then
  "$target/Contents/Resources/typhoon-backend" --json homekit enable >/dev/null
  # enable is idempotent; an unchanged plist can still reference a replaced
  # executable. Restart the job so this installation's code is actually used.
  /bin/launchctl kickstart -k "gui/$(id -u)/com.tuxpepe1893.typhoon.homekit"
fi
echo "Installed $target"
echo "CLI: $HOME/.local/bin/typhoon"
