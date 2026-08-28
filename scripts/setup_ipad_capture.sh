#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME="$ROOT/.local/runtime"
mkdir -p "$RUNTIME/bin" "$ROOT/.local/vendor"
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh" >&2; exit 1
fi
brew install cmake gstreamer libplist pkg-config
if [ ! -d "$ROOT/.local/vendor/UxPlay/.git" ]; then
  git clone --depth 1 https://github.com/FDH2/UxPlay.git "$ROOT/.local/vendor/UxPlay"
fi
cmake -S "$ROOT/.local/vendor/UxPlay" -B "$ROOT/.local/vendor/UxPlay/build" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$RUNTIME/uxplay"
cmake --build "$ROOT/.local/vendor/UxPlay/build" --parallel
cmake --install "$ROOT/.local/vendor/UxPlay/build"
swiftc "$ROOT/native/window_info.swift" -o "$RUNTIME/bin/window_info"
swiftc "$ROOT/native/ipad_usb_capture.swift" -o "$RUNTIME/bin/ipad_usb_capture"
echo "iPad capture runtime ready in $RUNTIME"
