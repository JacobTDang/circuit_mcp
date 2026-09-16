#!/usr/bin/env bash
# Build Andrew's PrepPal.app.
# Usage: macos/build_app.sh --stage python|app
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Andrew's PrepPal"
APP="$ROOT/dist/$APP_NAME.app"
RESOURCES="$APP/Contents/Resources"
WORK="$ROOT/build/macos"
SITE="$RESOURCES/python/lib/python3.12/site-packages"
# Quitting at the first match rather than piping into head: under 'set -o pipefail' a second
# top-level 'version = ' line closes the pipe on sed, and the script would abort on that EPIPE
# (141) with nothing said about which line it read.
VERSION="$(sed -n '/^version = "/{s/^version = "\(.*\)"$/\1/p;q;}' "$ROOT/pyproject.toml")"

fail() { echo "build_app: $*" >&2; exit 1; }

stage_python() {
  command -v uv >/dev/null 2>&1 || fail "uv is required: https://docs.astral.sh/uv/"
  (cd "$ROOT" && uv lock --check) || fail "uv.lock is out of date; run 'uv lock' and commit it"

  local found base bundled
  found="$(cd /tmp && uv python find --managed-python --no-project 3.12)" \
    || fail "no uv-managed Python 3.12; run 'uv python install 3.12'"
  base="$(dirname "$(dirname "$(realpath "$found")")")"

  rm -rf "$RESOURCES/python" "$WORK"
  mkdir -p "$RESOURCES" "$WORK"
  ditto "$base" "$RESOURCES/python"
  # This copy belongs to the app, and installing packages into it is intended.
  rm -f "$RESOURCES/python/lib/python3.12/EXTERNALLY-MANAGED"

  bundled="$RESOURCES/python/bin/python3"
  [ -x "$bundled" ] || fail "the copied Python has no bin/python3"

  (cd "$ROOT" && uv export --frozen --no-dev --no-hashes --no-emit-project \
      --format requirements-txt -o "$WORK/requirements.txt")
  (cd "$ROOT" && uv build --wheel --out-dir "$WORK/wheel")
  uv pip install --python "$bundled" --no-deps -r "$WORK/requirements.txt"
  uv pip install --python "$bundled" --no-deps "$WORK"/wheel/circuit_mcp-*.whl

  find "$SITE" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +
  "$bundled" -m compileall -q "$SITE" >"$WORK/compileall.log" \
    || { cat "$WORK/compileall.log" >&2; fail "compileall reported errors (listed above)"; }
}

stage_app() {
  [ -x "$RESOURCES/python/bin/python3" ] || fail "run 'macos/build_app.sh --stage python' first"
  [ -n "$VERSION" ] || fail "could not read the version from pyproject.toml"
  (cd "$ROOT/macos" && swift build -c release --product PrepPal)
  local bin
  bin="$(cd "$ROOT/macos" && swift build -c release --show-bin-path)/PrepPal"
  mkdir -p "$APP/Contents/MacOS"
  cp "$bin" "$APP/Contents/MacOS/PrepPal"
  sed "s/__VERSION__/$VERSION/g" "$ROOT/macos/Resources/Info.plist" >"$APP/Contents/Info.plist"
  plutil -lint "$APP/Contents/Info.plist" >/dev/null || fail "the generated Info.plist is invalid"
}

stage="${2:-}"
[ "${1:-}" = "--stage" ] || fail "usage: macos/build_app.sh --stage python|app"
case "$stage" in
  python) stage_python ;;
  app)    stage_app ;;
  *) fail "unknown stage '$stage'; expected: python or app" ;;
esac
echo "build_app: stage '$stage' done -> $APP"
