#!/usr/bin/env bash
# Rebuild macos/Resources/AppIcon.icns from macos/make_app_icon.swift.
# swiftc, sips and iconutil ship with macOS. The generated .icns is committed
# so a build of the app does not have to redraw it, and this script is what
# makes that file reproducible.
set -euo pipefail

MACOS="$(cd "$(dirname "$0")" && pwd)"
OUT="$MACOS/Resources/AppIcon.icns"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "make_app_icon: $*" >&2; exit 1; }

command -v swiftc >/dev/null 2>&1 || fail "swiftc is required"
command -v sips >/dev/null 2>&1 || fail "sips is required"
command -v iconutil >/dev/null 2>&1 || fail "iconutil is required"

swiftc "$MACOS/make_app_icon.swift" -o "$WORK/make_app_icon" || fail "could not compile the icon drawing"
"$WORK/make_app_icon" "$WORK/icon_1024.png" || fail "the icon drawing failed"

ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
# iconutil wants both the 1x name and the @2x name. Several sizes are the same
# pixels, so those are copied rather than resampled twice.
cp "$WORK/icon_1024.png" "$ICONSET/icon_512x512@2x.png"
sips -z 512 512 "$WORK/icon_1024.png" --out "$ICONSET/icon_512x512.png" >/dev/null
cp "$ICONSET/icon_512x512.png" "$ICONSET/icon_256x256@2x.png"
sips -z 256 256 "$WORK/icon_1024.png" --out "$ICONSET/icon_256x256.png" >/dev/null
cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_128x128@2x.png"
sips -z 128 128 "$WORK/icon_1024.png" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 64 64 "$WORK/icon_1024.png" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 32 32 "$WORK/icon_1024.png" --out "$ICONSET/icon_32x32.png" >/dev/null
cp "$ICONSET/icon_32x32.png" "$ICONSET/icon_16x16@2x.png"
sips -z 16 16 "$WORK/icon_1024.png" --out "$ICONSET/icon_16x16.png" >/dev/null

iconutil -c icns "$ICONSET" -o "$OUT" || fail "iconutil could not build $OUT"
[ -s "$OUT" ] || fail "iconutil wrote an empty icon"
echo "make_app_icon: wrote $OUT"
