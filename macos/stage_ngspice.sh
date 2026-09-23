#!/usr/bin/env bash
# Stage ngspice and every library it loads into a self-contained folder.
# Usage: macos/stage_ngspice.sh <destination>
#
# Homebrew's ngspice names its libraries by absolute path -- /opt/homebrew/opt/...
# -- which exists on the machine that built the app and on no machine that only
# opens it. Copying the binary alone therefore produces a simulator that runs
# here and dies there, and dies at launch, before any error this project writes.
# So the whole closure travels with it and every load command is rewritten to
# point inside the folder. The verify step at the end is the part that matters:
# it reads back what the staged files load rather than trusting the rewrite.
set -euo pipefail

fail() { echo "stage_ngspice: $*" >&2; exit 1; }

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

# A load command that already points inside a bundle, or at the OS itself, is
# not ours to move. Everything else is a path on this machine.
travels() {
  case "$1" in
    /usr/lib/*|/System/*|@*) return 1 ;;
    *) return 0 ;;
  esac
}

loads() { otool -L "$1" | tail -n +2 | awk '{print $1}'; }

# macOS ships bash 3.2, which has no associative arrays: the staged names are
# kept as one space-delimited string instead.
staged=" "
staged_count=0
pending=("$BINDIR/ngspice")
while [ ${#pending[@]} -gt 0 ]; do
  current="${pending[0]}"
  pending=("${pending[@]:1}")
  while IFS= read -r load; do
    travels "$load" || continue
    name="$(basename "$load")"
    case "$staged" in *" $name "*) continue ;; esac
    [ -f "$load" ] || fail "$load, loaded by $(basename "$current"), is not on this machine"
    cp "$load" "$LIBDIR/$name"
    chmod u+w "$LIBDIR/$name"
    staged="$staged$name "
    staged_count=$((staged_count + 1))
    pending+=("$LIBDIR/$name")
  done < <(loads "$current")
done
echo "stage_ngspice: staged $(basename "$SOURCE") and $staged_count librar$([ "$staged_count" -eq 1 ] && echo y || echo ies)"

# Editing a Mach-O file invalidates its signature, and install_name_tool warns
# about that once per edit. Dropping the signature up front says the same thing
# once, deliberately, and leaves the build output readable; every file is
# re-signed below before anything is asked to run it.
for target in "$BINDIR/ngspice" "$LIBDIR"/*.dylib; do
  codesign --remove-signature "$target" 2>/dev/null || true
done

# Naming each library by its own @rpath first means the -change pass below still
# sees the original absolute path in every other file that loads it.
for library in "$LIBDIR"/*.dylib; do
  install_name_tool -id "@rpath/$(basename "$library")" "$library"
done
for target in "$BINDIR/ngspice" "$LIBDIR"/*.dylib; do
  while IFS= read -r load; do
    travels "$load" || continue
    install_name_tool -change "$load" "@rpath/$(basename "$load")" "$target"
  done < <(loads "$target")
done
install_name_tool -add_rpath "@executable_path/../lib" "$BINDIR/ngspice"
for library in "$LIBDIR"/*.dylib; do
  install_name_tool -add_rpath "@loader_path" "$library"
done

# On Apple silicon an unsigned binary is killed on launch, not merely distrusted.
for target in "$BINDIR/ngspice" "$LIBDIR"/*.dylib; do
  codesign --force --sign - "$target" >/dev/null 2>&1 \
    || fail "could not re-sign $(basename "$target") after rewriting its load commands"
done

for target in "$BINDIR/ngspice" "$LIBDIR"/*.dylib; do
  while IFS= read -r load; do
    case "$load" in
      /usr/lib/*|/System/*) continue ;;
      @rpath/*)
        [ -f "$LIBDIR/${load#@rpath/}" ] \
          || fail "$(basename "$target") loads $load and no such library was staged"
        continue ;;
    esac
    fail "$(basename "$target") still loads $load, which is a path on this machine"
  done < <(loads "$target")
done

"$BINDIR/ngspice" --version >/dev/null 2>&1 \
  || fail "the staged ngspice does not run; its libraries or its signature are wrong"
echo "stage_ngspice: $DEST"
