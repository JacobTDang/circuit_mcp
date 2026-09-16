#!/usr/bin/env bash
# End to end: open the built app in a throwaway home, wait for a healthy server,
# quit the app, and confirm nothing from the bundle is left running.
# Usage: macos/smoke_test.sh [path/to/Andrew's PrepPal.app]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# bash 3.2 parses the apostrophe inside a ${...:-default} as an unterminated quote, so the
# default is assigned on its own line -- the same shape check_python_runtime.sh uses.
APP="${1:-}"
[ -n "$APP" ] || APP="$ROOT/dist/Andrew's PrepPal.app"
fail() { echo "smoke_test: $*" >&2; exit 1; }

[ -x "$APP/Contents/MacOS/PrepPal" ] || fail "no built app at $APP; run macos/build_app.sh"
# codesign refuses to look at a bundle whose root carries a com.apple.FinderInfo attribute, and a
# checkout under a synced folder gets one put back on every .app within a second or two of its
# being removed, so a strict check run in place answers by luck. Checking a copy in a temporary
# directory asks the same question of the same bytes and gets an answer decided by the signature.
# It is also the shape a user's copy has, dragged out of the disk image and out of the checkout.
# build_app.sh signs the same way, and explains it at more length.
check="$(mktemp -d)"
ditto "$APP" "$check/app"
case " $(xattr "$check/app" | tr '\n' ' ') " in   # ditto brought the source's attributes along
  *" com.apple.FinderInfo "*) xattr -d com.apple.FinderInfo "$check/app" ;;
esac
signed=0
codesign --verify --deep --strict "$check/app" || signed=$?
rm -rf "$check"
[ "$signed" -eq 0 ] || fail "the app is not signed; run macos/build_app.sh"
if pgrep -f "$APP/Contents/" >/dev/null; then fail "the app is already running; quit it first"; fi

home="$(mktemp -d)"
logs="$home/Logs/PrepPal"
open --env "PREPPAL_HOME=$home" "$APP"

port=""
for _ in $(seq 1 120); do
  log="$(ls -t "$logs"/server-*.log 2>/dev/null | head -1 || true)"
  if [ -n "$log" ]; then
    line="$(grep -m1 -E '^(READY|LOCKED) ' "$log" || true)"
    case "$line" in
      READY\ *) port="${line#READY }"; break ;;
      LOCKED\ *) cat "$log" >&2; fail "server reported: $line" ;;
    esac
  fi
  sleep 0.5
done
[ -n "$port" ] || fail "no READY line within 60 s (logs in $logs)"

healthy=""
for _ in $(seq 1 60); do
  # Not piped into grep: under 'set -o pipefail' a body large enough for grep -q to stop reading
  # first would close the pipe on curl, and a healthy server would read as an unhealthy one.
  if body="$(curl -fsS "http://127.0.0.1:$port/api/status" 2>/dev/null)" \
     && grep -q '"ok":true' <<<"$body"; then healthy=yes; break; fi
  sleep 0.5
done
[ -n "$healthy" ] || fail "/api/status on port $port never reported ok"

osascript -e 'tell application id "io.github.jacobtdang.preppal" to quit' \
  || fail "could not tell the app to quit; allow the terminal to control other apps in System Settings -> Privacy & Security -> Automation"
for _ in $(seq 1 40); do
  pgrep -f "$APP/Contents/" >/dev/null || break
  sleep 0.5
done
if pgrep -fl "$APP/Contents/"; then fail "processes from the app are still running 20 s after quit"; fi

rm -rf "$home"
echo "smoke_test: the app served port $port, quit cleanly, and left nothing running"
