#!/usr/bin/env bash
# Prove the app's bundled Python is self-contained and runs the command-center server.
# Usage: macos/check_python_runtime.sh [path/to/Circuit MCP.app]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-}"
[ -n "$APP" ] || APP="$ROOT/dist/Circuit MCP.app"
PY="$APP/Contents/Resources/python/bin/python3"
fail() { echo "check_python_runtime: $*" >&2; exit 1; }

[ -x "$PY" ] || fail "no bundled Python at $PY"
expected_prefix="$(cd "$APP/Contents/Resources/python" && pwd -P)"

# A clean environment proves nothing leaks in from the repo .venv or PYTHONPATH.
clean=(env -i "HOME=$HOME" "PATH=/usr/bin:/bin")

prefix="$("${clean[@]}" "$PY" -c 'import os, sys; print(os.path.realpath(sys.prefix))')"
[ "$prefix" = "$expected_prefix" ] || fail "sys.prefix is $prefix, expected $expected_prefix"

"${clean[@]}" "$PY" -c '
import circuit_mcp, sys
assert "/Contents/Resources/python/" in circuit_mcp.__file__, circuit_mcp.__file__
import circuit_mcp.web  # every web dependency imports from the bundle
' || fail "circuit_mcp does not import from the bundle"

work="$(mktemp -d)"
log="$work/server.log"
"${clean[@]}" "$PY" -m circuit_mcp.app_server --data-dir "$work/data" >"$log" 2>&1 &
pid=$!

line=""
for _ in $(seq 1 120); do
  line="$(grep -m1 -E '^(READY|LOCKED) ' "$log" || true)"
  [ -n "$line" ] && break
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.5
done

if [ "${line%% *}" != "READY" ]; then
  kill -TERM "$pid" 2>/dev/null || true
  cat "$log" >&2
  fail "server did not report READY"
fi
port="${line#READY }"

if ! body="$(curl -fsS "http://127.0.0.1:$port/api/status")" || ! grep -q '"ok":true' <<<"$body"; then
  kill -TERM "$pid" 2>/dev/null || true
  cat "$log" >&2
  fail "/api/status on port $port is not ok"
fi

kill -TERM "$pid" 2>/dev/null || { cat "$log" >&2; fail "server was already gone before shutdown"; }
if wait "$pid"; then status=0; else status=$?; fi
[ "$status" -eq 0 ] || { cat "$log" >&2; fail "server exited $status after SIGTERM"; }

rm -rf "$work"
echo "check_python_runtime: bundled Python served port $port and stopped cleanly"
