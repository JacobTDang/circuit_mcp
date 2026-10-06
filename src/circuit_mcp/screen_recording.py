"""Whether this process may record the screen, asked of macOS rather than guessed.

Run from a terminal, the permission is inherited from the terminal and never
came up. A packaged app is its own subject. Before it has prompted, macOS gives
the same "not allowed" answer as a denial, so the desk reports
``not_determined`` until the user asks. A persisted marker records that the
prompt has happened; only then can the same answer honestly mean ``denied``.

CoreGraphics answers both questions and is loaded through ``ctypes``, which is
in the standard library: a permission check is not worth a dependency. Off
macOS, or on a build without the symbols, the state is "unavailable" and the
capture tool itself stays the authority -- an unknown permission is not a
refusal.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from . import paths

FRAMEWORK = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
GRANTED = "granted"
DENIED = "denied"
UNAVAILABLE = "unavailable"
NOT_DETERMINED = "not_determined"

_LIBRARY: ctypes.CDLL | None = None
_LOADED = False
_ASKED = False


def _core_graphics() -> ctypes.CDLL | None:
    """The framework, loaded once. None where it does not exist or has no symbols."""
    global _LIBRARY, _LOADED
    if _LOADED:
        return _LIBRARY
    _LOADED = True
    if sys.platform != "darwin":
        return None
    try:
        library = ctypes.CDLL(FRAMEWORK)
        for name in ("CGPreflightScreenCaptureAccess", "CGRequestScreenCaptureAccess"):
            function = getattr(library, name)
            function.restype = ctypes.c_bool
            function.argtypes = []
    except (OSError, AttributeError):
        return None
    _LIBRARY = library
    return _LIBRARY


def _marker() -> Path:
    return paths.runtime_dir() / "screen_recording_requested"


def permission_state() -> str:
    """"granted", "denied", "not_determined", or "unavailable". Never prompts."""
    library = _core_graphics()
    if library is None:
        return UNAVAILABLE
    if library.CGPreflightScreenCaptureAccess():
        return GRANTED
    if _marker().is_file():
        return DENIED
    return NOT_DETERMINED


def request_access() -> str:
    """Trigger the one-time system prompt, then report where that left things.

    Once per process: macOS shows the panel the first time and answers from the
    stored decision afterwards, so asking on every capture would add nothing and
    would make a denied install look like it was nagging.
    """
    global _ASKED
    library = _core_graphics()
    if library is None:
        return UNAVAILABLE
    if not _ASKED:
        _ASKED = True
        marker = _marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        library.CGRequestScreenCaptureAccess()
    return permission_state()


DENIED_MESSAGE = (
    "Screen Recording is not allowed for this app, so macOS will not hand over any "
    "pixels. Open System Settings > Privacy & Security > Screen & System Audio "
    "Recording, switch this app on, then quit and reopen it and try again."
)
