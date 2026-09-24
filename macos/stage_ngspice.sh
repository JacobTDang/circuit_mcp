#!/usr/bin/env bash
# Stage ngspice and every library it loads into a self-contained folder.
# Usage: macos/stage_ngspice.sh <destination>
#
# The relocation itself lives in relocate.sh, which stage_uxplay.sh uses too.
# The verify step at the end is the part that matters: it reads back what the
# staged files load rather than trusting the rewrite.
set -euo pipefail

RELOCATE_TAG=stage_ngspice
. "$(cd "$(dirname "$0")" && pwd)/relocate.sh"
fail() { relocate_fail "$@"; }

DEST="${1:-}"
[ -n "$DEST" ] || fail "usage: stage_ngspice.sh <destination>"

SOURCE="${NGSPICE:-$(command -v ngspice || true)}"
[ -n "$SOURCE" ] || fail "no ngspice on PATH to stage; install it with 'brew install ngspice'"
[ -x "$SOURCE" ] || fail "$SOURCE is not an executable ngspice"
SOURCE="$(realpath "$SOURCE")"

BINDIR="$DEST/bin"
LIBDIR="$DEST/lib"
rm -rf "$DEST"
mkdir -p "$BINDIR" "$LIBDIR"
cp "$SOURCE" "$BINDIR/ngspice"
chmod u+w "$BINDIR/ngspice"

relocate_collect "$LIBDIR" "$BINDIR/ngspice"
echo "stage_ngspice: staged $(basename "$SOURCE") and $RELOCATE_COUNT librar$([ "$RELOCATE_COUNT" -eq 1 ] && echo y || echo ies)"

relocate_unsign "$BINDIR/ngspice" "$LIBDIR"/*.dylib
relocate_id "$LIBDIR"
relocate_point_at "@loader_path" "$LIBDIR"/*.dylib
relocate_point_at "@executable_path/../lib" "$BINDIR/ngspice"
relocate_sign "$BINDIR/ngspice" "$LIBDIR"/*.dylib
relocate_verify "$LIBDIR" "$BINDIR/ngspice" "$LIBDIR"/*.dylib

"$BINDIR/ngspice" --version >/dev/null 2>&1 \
  || fail "the staged ngspice does not run; its libraries or its signature are wrong"
echo "stage_ngspice: $DEST"
