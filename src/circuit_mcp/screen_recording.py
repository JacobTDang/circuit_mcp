"""Whether this process may record the screen, asked of macOS rather than guessed.

Run from a terminal, the permission is inherited from the terminal and never
came up. A packaged app is its own subject: macOS asks once, against the app's
bundle identifier, and until it is granted every capture fails. So the desk has
to be able to say "denied" before a button is pressed, and something has to
trigger that one prompt -- nothing else in the app ever would, because
``screencapture`` is a separate binary and the answer it gives back is about
itself.

CoreGraphics answers both questions and is loaded through ``ctypes``, which is
in the standard library: a permission check is not worth a dependency. Off
macOS, or on a build without the symbols, the state is "unavailable" and the
capture tool itself stays the authority -- an unknown permission is not a
refusal.
"""
from __future__ import annotations

import ctypes
import sys

FRAMEWORK = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
GRANTED = "granted"
DENIED = "denied"
UNAVAILABLE = "unavailable"

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


def permission_state() -> str:
    """"granted", "denied", or "unavailable". Never prompts."""
    library = _core_graphics()
    if library is None:
        return UNAVAILABLE
    return GRANTED if library.CGPreflightScreenCaptureAccess() else DENIED


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
        library.CGRequestScreenCaptureAccess()
    return GRANTED if library.CGPreflightScreenCaptureAccess() else DENIED


DENIED_MESSAGE = (
    "Screen Recording is not allowed for this app, so macOS will not hand over any "
    "pixels. Open System Settings > Privacy & Security > Screen & System Audio "
    "Recording, switch this app on, then quit and reopen it and try again."
)
