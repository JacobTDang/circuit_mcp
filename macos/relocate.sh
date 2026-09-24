# Make a copied Mach-O tree self-contained. Sourced, not run.
#
# Homebrew names its libraries by absolute path -- /opt/homebrew/opt/... -- which
# exists on the machine that built the app and on no machine that only opens it.
# Copying a binary alone therefore produces something that runs here and dies
# there, at launch, before any error this project writes. So the whole closure
# travels with it and every load command is rewritten to point inside the tree.
#
# macOS ships bash 3.2, which has no associative arrays: the set of staged names
# is kept as one space-delimited string instead.

relocate_fail() { echo "${RELOCATE_TAG:-relocate}: $*" >&2; exit 1; }

# A load command that already points inside a bundle, or at the OS itself, is not
# ours to move. Everything else is a path on this machine.
relocate_travels() {
  case "$1" in
    /usr/lib/*|/System/*|@*) return 1 ;;
    *) return 0 ;;
  esac
}

relocate_loads() { otool -L "$1" | tail -n +2 | awk '{print $1}'; }

# relocate_collect <libdir> <file>...
# Copies every non-system library the files load, transitively, into <libdir>.
# The files themselves stay where they are; only their dependencies are copied.
relocate_collect() {
  local libdir="$1"; shift
  local staged=" " current load name
  local pending=("$@")
  RELOCATE_COUNT=0
  mkdir -p "$libdir"
  while [ ${#pending[@]} -gt 0 ]; do
    current="${pending[0]}"
    pending=("${pending[@]:1}")
    while IFS= read -r load; do
      relocate_travels "$load" || continue
      name="$(basename "$load")"
      case "$staged" in *" $name "*) continue ;; esac
      [ -f "$load" ] || relocate_fail "$load, loaded by $(basename "$current"), is not on this machine"
      cp "$load" "$libdir/$name"
      chmod u+w "$libdir/$name"
      staged="$staged$name "
      RELOCATE_COUNT=$((RELOCATE_COUNT + 1))
      pending+=("$libdir/$name")
    done < <(relocate_loads "$current")
  done
}

# Editing a Mach-O file invalidates its signature, and install_name_tool warns
# about that once per edit. Dropping the signature up front says the same thing
# once, deliberately, and leaves the build output readable.
relocate_unsign() {
  local target
  for target in "$@"; do codesign --remove-signature "$target" 2>/dev/null || true; done
}

# Name each library by its own @rpath. Done before any -change pass, so that pass
# still sees the original absolute path in every file that loads it.
relocate_id() {
  local library
  for library in "$1"/*.dylib; do
    [ -f "$library" ] || continue
    install_name_tool -id "@rpath/$(basename "$library")" "$library"
  done
}

# relocate_point_at <rpath> <file>...
relocate_point_at() {
  local rpath="$1"; shift
  local target load
  for target in "$@"; do
    [ -f "$target" ] || continue
    while IFS= read -r load; do
      relocate_travels "$load" || continue
      install_name_tool -change "$load" "@rpath/$(basename "$load")" "$target"
    done < <(relocate_loads "$target")
    install_name_tool -add_rpath "$rpath" "$target" 2>/dev/null || true
  done
}

# On Apple silicon an unsigned binary is killed on launch, not merely distrusted.
relocate_sign() {
  local target
  for target in "$@"; do
    [ -f "$target" ] || continue
    codesign --force --sign - "$target" >/dev/null 2>&1 \
      || relocate_fail "could not re-sign $(basename "$target") after rewriting its load commands"
  done
}

# The step that matters: read back what the staged files load rather than
# trusting the rewrite.
relocate_verify() {
  local libdir="$1"; shift
  local target load
  for target in "$@"; do
    [ -f "$target" ] || continue
    while IFS= read -r load; do
      case "$load" in
        /usr/lib/*|/System/*) continue ;;
        @rpath/*)
          [ -f "$libdir/${load#@rpath/}" ] \
            || relocate_fail "$(basename "$target") loads $load and no such library was staged"
          continue ;;
      esac
      relocate_fail "$(basename "$target") still loads $load, which is a path on this machine"
    done < <(relocate_loads "$target")
  done
}
