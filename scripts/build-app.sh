#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# TYPHOON_BUILD_CONFIG=debug builds the Swift front end without optimization (used by CI).
CONFIG="${TYPHOON_BUILD_CONFIG:-release}"
uv sync --extra dev
swift build -c "$CONFIG" --product Typhoon
.venv/bin/pyinstaller --noconfirm --clean --onedir --paths . \
  --name typhoon-backend --collect-data pyhap --distpath build/backend --workpath build/pyinstaller \
  --specpath build scripts/backend-entry.py
APP="build/Typhoon.app"
# This is exclusively generated output. Reassemble it to avoid stale runtimes.
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp ".build/$CONFIG/Typhoon" "$APP/Contents/MacOS/Typhoon"
cp -R build/backend/typhoon-backend/. "$APP/Contents/Resources/"
cp packaging/Info.plist "$APP/Contents/Info.plist"
.venv/bin/python scripts/collect-licenses.py "$APP/Contents/Resources/Licenses"
cp LICENSE "$APP/Contents/Resources/Licenses/Typhoon-LICENSE.txt"
cp docs/third-party-cloud.md "$APP/Contents/Resources/Licenses/Tornado-cloud.md"
swift scripts/make-icon.swift build/Typhoon.iconset
iconutil -c icns build/Typhoon.iconset -o "$APP/Contents/Resources/Typhoon.icns"
codesign --force --deep --sign - "$APP"
if [[ -n "${TYPHOON_SIGNING_IDENTITY:-}" ]]; then
  # Keep the network service's identity stable across PyInstaller rebuilds.
  codesign --force --timestamp --identifier com.tuxpepe1893.typhoon.backend \
    --sign "$TYPHOON_SIGNING_IDENTITY" "$APP/Contents/Resources/typhoon-backend"
  codesign --force --timestamp --sign "$TYPHOON_SIGNING_IDENTITY" "$APP"
fi
codesign --verify --deep --strict "$APP"
echo "Built $PWD/$APP"
