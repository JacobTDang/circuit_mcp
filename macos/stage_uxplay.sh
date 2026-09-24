#!/usr/bin/env bash
# Stage the AirPlay receiver and the GStreamer it needs into a self-contained folder.
# Usage: macos/stage_uxplay.sh <destination>
#
# UxPlay is GStreamer, and GStreamer is not one binary: it loads plugins at run
# time from a path it is told about, and spawns a scanner to index them. So this
# stages three things -- the receiver, the plugins, and the scanner -- and the
# app has to name all three in the environment or the bundled copy is ignored in
# favour of whatever the machine happens to have.
#
# Only the plugins the headless pipeline uses are staged. UxPlay is started with
# '-vs fakesink -as 0', so there is no window and no audio, and the pipeline is
#     appsrc ! queue ! h264parse ! decodebin ! videoconvert ! videoscale ! fakesink
# The decoder decodebin auto-plugs is vtdec_hw from applemedia, which is
# VideoToolbox and therefore present on every Mac. That is what lets this be
# 31 MB instead of the 162 MB of plugins Homebrew installs, and it is why
# libgstlibav -- which would drag in the whole of ffmpeg -- is left out.
set -euo pipefail

RELOCATE_TAG=stage_uxplay
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/relocate.sh"
fail() { relocate_fail "$@"; }

DEST="${1:-}"
[ -n "$DEST" ] || fail "usage: stage_uxplay.sh <destination>"

ROOT="$(cd "$HERE/.." && pwd)"
SOURCE="${UXPLAY:-$ROOT/.local/runtime/uxplay/bin/uxplay}"
[ -x "$SOURCE" ] || fail "no uxplay to stage at $SOURCE; build one with scripts/setup_ipad_capture.sh"
SOURCE="$(realpath "$SOURCE")"

command -v pkg-config >/dev/null 2>&1 || fail "pkg-config is required to locate GStreamer"
GST_PREFIX="$(pkg-config --variable=prefix gstreamer-1.0)" \
  || fail "pkg-config cannot find gstreamer-1.0"
GST_PLUGINS="$GST_PREFIX/lib/gstreamer-1.0"
SCANNER="$GST_PREFIX/libexec/gstreamer-1.0/gst-plugin-scanner"
[ -d "$GST_PLUGINS" ] || fail "no GStreamer plugins at $GST_PLUGINS"
[ -x "$SCANNER" ] || fail "no gst-plugin-scanner at $SCANNER"

# Two lists in one. UxPlay refuses to start unless the registry holds app, libav,
# playback, autodetect and videoparsersbad -- a fixed check in audio_renderer.c,
# made before any pipeline exists and regardless of the flags. The rest are what
# the headless pipeline then resolves. libav is why this is 88 MB and not 22: it
# drags in ffmpeg, and there is no asking UxPlay to skip the check.
PLUGINS="app libav playback autodetect videoparsersbad
         coreelements videoconvertscale applemedia typefindfunctions"

BINDIR="$DEST/bin"
LIBDIR="$DEST/lib"
PLUGDIR="$DEST/plugins"
LIBEXEC="$DEST/libexec"
rm -rf "$DEST"
mkdir -p "$BINDIR" "$LIBDIR" "$PLUGDIR" "$LIBEXEC"

cp "$SOURCE" "$BINDIR/uxplay"
cp "$SCANNER" "$LIBEXEC/gst-plugin-scanner"
# gst-inspect is how a broken install is diagnosed on a Mac that is not this one,
# and how the build proves every element the pipeline needs actually resolves.
INSPECT="$GST_PREFIX/bin/gst-inspect-1.0"
[ -x "$INSPECT" ] || fail "no gst-inspect-1.0 at $INSPECT"
cp "$INSPECT" "$BINDIR/gst-inspect-1.0"
chmod u+w "$BINDIR/uxplay" "$BINDIR/gst-inspect-1.0" "$LIBEXEC/gst-plugin-scanner"

for name in $PLUGINS; do
  [ -f "$GST_PLUGINS/libgst$name.dylib" ] || fail "no libgst$name.dylib in $GST_PLUGINS"
  cp "$GST_PLUGINS/libgst$name.dylib" "$PLUGDIR/"
  chmod u+w "$PLUGDIR/libgst$name.dylib"
done

relocate_collect "$LIBDIR" "$BINDIR/uxplay" "$BINDIR/gst-inspect-1.0" \
  "$LIBEXEC/gst-plugin-scanner" "$PLUGDIR"/*.dylib
echo "stage_uxplay: staged uxplay, $(ls "$PLUGDIR" | wc -l | tr -d ' ') plugins and $RELOCATE_COUNT libraries"

relocate_unsign "$BINDIR"/* "$LIBEXEC"/* "$PLUGDIR"/*.dylib "$LIBDIR"/*.dylib
relocate_id "$LIBDIR"
# A plugin's own install name is the Homebrew path it was built at. GStreamer
# dlopens plugins by file path so nothing resolves through that id, but leaving
# it absolute means the staged tree still names this machine -- and the verify
# below reads ids as well as loads, on purpose.
relocate_id "$PLUGDIR"
relocate_point_at "@loader_path" "$LIBDIR"/*.dylib
relocate_point_at "@loader_path/../lib" "$PLUGDIR"/*.dylib
relocate_point_at "@executable_path/../lib" "$BINDIR"/*
relocate_point_at "@executable_path/../lib" "$LIBEXEC"/*
relocate_sign "$BINDIR"/* "$LIBEXEC"/* "$PLUGDIR"/*.dylib "$LIBDIR"/*.dylib
relocate_verify "$LIBDIR" "$BINDIR"/* "$LIBEXEC"/* "$PLUGDIR"/*.dylib "$LIBDIR"/*.dylib

# The registry goes somewhere writable and throwaway for this check: inside a
# real .app the folder is read-only, which is why the app names its own.
registry="$(mktemp -t uxplay-registry)"
trap 'rm -f "$registry"' EXIT
export GST_PLUGIN_SYSTEM_PATH="$PLUGDIR" GST_PLUGIN_PATH="$PLUGDIR" \
       GST_PLUGIN_SCANNER="$LIBEXEC/gst-plugin-scanner" GST_REGISTRY="$registry"
for element in appsrc queue h264parse decodebin videoconvert videoscale fakesink vtdec_hw; do
  "$BINDIR/gst-inspect-1.0" --exists "$element" \
    || fail "the staged plugins cannot provide '$element', so the pipeline would fail at run time"
done
# -v only prints a version string. UxPlay's plugin check happens on the way to
# serving, so the only way to know the staged registry satisfies it is to start
# one and watch. A bundle that passes every otool and gst-inspect check above can
# still refuse here, which is exactly how this step earned its place.
started="$(mktemp -t uxplay-start)"
"$BINDIR/uxplay" -n "Staging Check" -nh -vs fakesink -as 0 >"$started" 2>&1 &
receiver=$!
sleep 4
if grep -q "not found" "$started" || ! kill -0 "$receiver" 2>/dev/null; then
  kill "$receiver" 2>/dev/null || true
  echo "--- uxplay said ---" >&2
  sed -n "1,25p" "$started" >&2
  rm -f "$started"
  fail "the staged uxplay refuses to start; a plugin it requires was not staged"
fi
kill "$receiver" 2>/dev/null || true
wait "$receiver" 2>/dev/null || true
rm -f "$started"
echo "stage_uxplay: $DEST"
