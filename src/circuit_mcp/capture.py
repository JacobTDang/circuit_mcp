"""Capture the mirrored iPad workspace on macOS.

Capture is deliberately request-driven. Nothing records in the background: an
MCP client asks for the current frame when the student asks it to inspect their
work. macOS remains the authority for Screen Recording permission.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

SCREEN_CAPTURE = "/usr/sbin/screencapture"
MAX_CAPTURE_BYTES = 25 * 1024 * 1024


class CaptureError(RuntimeError):
    """The workspace could not be captured safely."""


def capture_status() -> dict:
    """Report platform/tool availability without triggering a privacy prompt."""
    executable = shutil.which(SCREEN_CAPTURE)
    return {
        "ok": executable is not None,
        "platform": "macos" if executable else "unsupported",
        "capture_command": executable,
        "permission": "unknown_until_capture",
        "message": (
            "Screen capture is available. macOS may ask for Screen Recording "
            "permission on the first capture."
            if executable
            else "This capture backend requires macOS /usr/sbin/screencapture."
        ),
    }


def _region(x: int | None, y: int | None, width: int | None, height: int | None):
    values = (x, y, width, height)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise CaptureError(
            "A capture region needs all four values: x, y, width, and height."
        )
    assert x is not None and y is not None and width is not None and height is not None
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise CaptureError(
            "Capture coordinates x/y must be nonnegative and width/height must "
            "be positive."
        )
    return x, y, width, height


def capture_workspace(
    display: int = 1,
    allow_full_display: bool = False,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict:
    """Capture a display or global screen rectangle and return its PNG bytes."""
    status = capture_status()
    if not status["ok"]:
        raise CaptureError(status["message"])
    if display < 1:
        raise CaptureError("display must be 1 or greater.")

    region = _region(x, y, width, height)
    if region is None and not allow_full_display:
        raise CaptureError(
            "Full-display capture can expose unrelated windows and notifications. "
            "Pass x, y, width, and height for the visible iPad screen. If "
            "the iPad mirror intentionally occupies the entire display, pass "
            "allow_full_display=true explicitly."
        )
    with tempfile.TemporaryDirectory(prefix="circuit-mcp-capture-") as directory:
        output = Path(directory) / "workspace.png"
        command = [SCREEN_CAPTURE, "-x", "-t", "png"]
        if region is None:
            command.extend(["-D", str(display)])
            selection = {"kind": "display", "display": display}
        else:
            command.extend(["-R", ",".join(map(str, region))])
            selection = {
                "kind": "region",
                "x": region[0],
                "y": region[1],
                "width": region[2],
                "height": region[3],
            }
        command.append(str(output))

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CaptureError(f"Could not run macOS screen capture: {exc}") from exc

        if completed.returncode != 0 or not output.exists():
            detail = completed.stderr.strip() or "no image was produced"
            raise CaptureError(
                "Screen capture failed. Allow Screen Recording for the app that "
                f"runs this MCP server in System Settings, then retry. Detail: {detail}"
            )

        png = output.read_bytes()

    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CaptureError("Screen capture returned data that is not a PNG image.")
    if len(png) > MAX_CAPTURE_BYTES:
        raise CaptureError(
            f"Captured image is {len(png)} bytes; limit is {MAX_CAPTURE_BYTES}. "
            "Capture a smaller iPad screen region."
        )

    return {
        "ok": True,
        "mime_type": "image/png",
        "bytes": len(png),
        "sha256": hashlib.sha256(png).hexdigest(),
        "selection": selection,
        "png": png,
    }
