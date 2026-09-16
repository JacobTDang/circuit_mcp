#!/usr/bin/env bash
# Build Andrew's PrepPal.app.
# Usage: macos/build_app.sh [--stage python|app|sign|dmg|all]
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
temporary_dirs=("")

fail() { echo "build_app: $*" >&2; exit 1; }

cleanup_build_temps() {
  local path
  for path in "${temporary_dirs[@]}"; do
    [ -n "$path" ] && rm -rf -- "$path"
  done
}
trap cleanup_build_temps EXIT

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
  case " $(lipo -archs "$bundled") " in
    *" arm64 "*) ;;
    *) fail "bundled Python is $(lipo -archs "$bundled"), expected arm64" ;;
  esac

  (cd "$ROOT" && uv export --frozen --no-dev --no-hashes --no-emit-project \
      --format requirements-txt -o "$WORK/requirements.txt")
  (cd "$ROOT" && uv build --wheel --out-dir "$WORK/wheel")
  uv pip install --python "$bundled" --no-deps -r "$WORK/requirements.txt"
  uv pip install --python "$bundled" --no-deps "$WORK"/wheel/circuit_mcp-*.whl

  local native arches
  while IFS= read -r native; do
    if file "$native" | grep -q 'Mach-O'; then
      arches="$(lipo -archs "$native")"
      case " $arches " in
        *" arm64 "*) ;;
        *) fail "bundled native library $native is $arches, expected arm64" ;;
      esac
    fi
  done < <(find "$RESOURCES/python" -type f \( -name '*.so' -o -name '*.dylib' \) -print)

  find "$SITE" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +
  "$bundled" -m compileall -q "$SITE" >"$WORK/compileall.log" \
    || { cat "$WORK/compileall.log" >&2; fail "compileall reported errors (listed above)"; }
  strip_console_scripts
}

# 'uv pip install' writes each console script (uvicorn, fastapi, ipython, ...) with this build
# machine's absolute path to the interpreter in its exec line, so every one of them stops working
# the moment the .app is dragged to /Applications -- which is exactly what the disk image invites.
# Nothing in the app runs them: the server starts as 'python3 -m circuit_mcp.app_server'. Rather
# than ship them broken they are removed here. The scripts the Python distribution itself ships
# (pip, pydoc, 2to3, idle, python3-config) exec through "$(dirname -- "$(realpath -- "$0")")"
# instead, so they move with the bundle and stay.
strip_console_scripts() {
  local script removed=0
  for script in "$RESOURCES"/python/bin/*; do
    if [ -L "$script" ] || [ ! -f "$script" ]; then continue; fi
    if [ "$(head -c 2 "$script")" != '#!' ]; then continue; fi
    case "$(LC_ALL=C sed -n 2p "$script")" in
      "'''exec' '"*) rm -f "$script"; removed=$((removed + 1)) ;;
    esac
  done
  # The build path has no apostrophe to quote, so it greps literally even though the path the
  # scripts embedded did not. Text files only: the interpreter next to them is a Mach-O binary.
  if grep -rIlF "$ROOT/" "$RESOURCES/python/bin" >/dev/null 2>&1; then
    fail "bin/ still names the build machine's path after removing $removed console script(s)"
  fi
  echo "build_app: removed $removed console script(s) that hardcoded the build path"
}

stage_app() {
  [ -x "$RESOURCES/python/bin/python3" ] || fail "run 'macos/build_app.sh --stage python' first"
  [ -n "$VERSION" ] || fail "could not read the version from pyproject.toml"
  # --arch arm64 on purpose: without it the slice is whatever the build host happens to be, so
  # the disk image would only be Apple silicon by accident. The bundled Python is an
  # aarch64 build, so arm64 is the only slice the app could run with anyway.
  (cd "$ROOT/macos" && swift build -c release --arch arm64 --product PrepPal)
  local bin
  bin="$(cd "$ROOT/macos" && swift build -c release --arch arm64 --show-bin-path)/PrepPal"
  [ "$(lipo -archs "$bin")" = "arm64" ] || fail "built $(lipo -archs "$bin"), expected arm64"
  mkdir -p "$APP/Contents/MacOS"
  cp "$bin" "$APP/Contents/MacOS/PrepPal"
  sed "s/__VERSION__/$VERSION/g" "$ROOT/macos/Resources/Info.plist" >"$APP/Contents/Info.plist"
  plutil -lint "$APP/Contents/Info.plist" >/dev/null || fail "the generated Info.plist is invalid"
}

# codesign refuses to sign or verify a bundle whose root carries a Finder info or resource fork
# attribute: "resource fork, Finder information, or similar detritus not allowed". This checkout
# sits under a Desktop synced by iCloud, and that file provider stamps every .app directory with
# the com.apple.FinderInfo package flag within a second or two of one appearing -- and puts it
# straight back when it is removed. Removing it and signing in place is therefore a race with a
# daemon, lost often enough to measure. So every codesign call here runs on a copy in a temporary
# directory, where nothing puts the flag back and the answer is decided by the signature alone.
# The flag is not part of a signature: a signed bundle stays signed when the provider stamps it.
clear_detritus() {
  local path="$1" name present
  present=" $(xattr "$path" | tr '\n' ' ') "
  for name in com.apple.FinderInfo com.apple.ResourceFork; do
    case "$present" in
      *" $name "*) xattr -d "$name" "$path" || fail "could not remove $name from $path" ;;
    esac
  done
}

# Copies the bundle out of the checkout and leaves the copy's path in `unsynced`. The caller
# removes `unsynced_work` when it is done with it.
copy_out_of_tree() {
  unsynced_work="$(mktemp -d)"
  temporary_dirs+=("$unsynced_work")
  unsynced="$unsynced_work/$APP_NAME.app"
  ditto "$1" "$unsynced"       # ditto copies the source's attributes along with its files
  clear_detritus "$unsynced"
}

stage_sign() {
  [ -x "$APP/Contents/MacOS/PrepPal" ] || fail "run 'macos/build_app.sh --stage app' first"
  local unsynced unsynced_work signed_candidate backup
  copy_out_of_tree "$APP"
  # Ad-hoc signature. A release build would add Developer ID signing, notarization, and stapling.
  codesign --force --deep --sign - "$unsynced" || fail "codesign could not sign the app"
  codesign --verify --deep --strict "$unsynced" || fail "codesign could not verify the signature"
  signed_candidate="$ROOT/dist/.$APP_NAME.signed.$$.app"
  backup="$ROOT/dist/.$APP_NAME.previous.$$.app"
  temporary_dirs+=("$signed_candidate" "$backup")
  ditto "$unsynced" "$signed_candidate" || fail "could not stage the signed app in dist"
  mv "$APP" "$backup" || fail "could not stage the unsigned app for replacement"
  if mv "$signed_candidate" "$APP"; then
    rm -rf "$backup"
  else
    if ! mv "$backup" "$APP"; then
      temporary_dirs=("")
      fail "could not install the signed app or restore it; recovery files remain in $ROOT/dist"
    fi
    fail "could not install the signed app; restored the unsigned app"
  fi
  rm -rf "$unsynced_work"
}

stage_dmg() {
  local dmg="$ROOT/dist/PrepPal-$VERSION.dmg"
  local unsynced unsynced_work dmg_work candidate
  # The staged copy is both the signature check and what the disk image is built from, so what
  # users open is the bundle that verified, without the sync flag on it.
  copy_out_of_tree "$APP"
  codesign --verify --deep --strict "$unsynced" \
    || fail "the app is not signed; run 'macos/build_app.sh --stage sign' first"
  ln -s /Applications "$unsynced_work/Applications"
  dmg_work="$(mktemp -d "$ROOT/dist/.preppal-dmg.XXXXXX")"
  temporary_dirs+=("$dmg_work")
  candidate="$dmg_work/PrepPal-$VERSION.dmg"
  hdiutil create -volname "$APP_NAME" -srcfolder "$unsynced_work" -ov -format UDZO "$candidate" >/dev/null
  hdiutil verify "$candidate" >/dev/null || fail "hdiutil could not verify $candidate"
  mv -f "$candidate" "$dmg"
  rm -rf "$unsynced_work"
  echo "build_app: disk image -> $dmg"
}

if [ "$#" -eq 0 ]; then
  stage=all
elif [ "$#" -eq 2 ] && [ "${1:-}" = "--stage" ] && [ -n "${2:-}" ]; then
  stage="$2"
else
  fail "usage: macos/build_app.sh [--stage python|app|sign|dmg|all]"
fi

case "$stage" in
  python) stage_python ;;
  app)    stage_app ;;
  sign)   stage_sign ;;
  dmg)    stage_dmg ;;
  all)    stage_python; stage_app; stage_sign; stage_dmg ;;
  *) fail "unknown stage '$stage'; expected python, app, sign, dmg, or all" ;;
esac
echo "build_app: stage '$stage' done -> $APP"
