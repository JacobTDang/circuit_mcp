"""Private, local configuration for an iPad-screen capture source."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .capture import CaptureError, _region

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / ".local/workspace.json"


def config_path() -> Path:
    return Path(
        os.environ.get("CIRCUIT_MCP_WORKSPACE_CONFIG", DEFAULT_CONFIG)
    ).expanduser().resolve()


def read_workspace() -> dict:
    path = config_path()
    if not path.exists():
        return {
            "ok": False,
            "configured": False,
            "path": str(path),
            "message": "No iPad screen source or capture region is configured.",
        }
    try:
        value = json.loads(path.read_text())
        if value.get("mode") == "display":
            display = int(value.get("display", 0))
            if display < 1:
                raise CaptureError("display must be 1 or greater.")
            return {"ok": True, "configured": True, "path": str(path),
                    "mode": "display", "display": display}
        region = _region(
            value.get("x"), value.get("y"), value.get("width"), value.get("height")
        )
    except (OSError, ValueError, TypeError, CaptureError) as exc:
        return {
            "ok": False,
            "configured": False,
            "path": str(path),
            "message": f"Workspace configuration is invalid: {exc}",
        }
    assert region is not None
    return {
        "ok": True,
        "configured": True,
        "path": str(path),
        "display": int(value.get("display", 1)),
        "x": region[0],
        "y": region[1],
        "width": region[2],
        "height": region[3],
    }


def configure_workspace(
    x: int, y: int, width: int, height: int, display: int = 1
) -> dict:
    region = _region(x, y, width, height)
    assert region is not None
    if display < 1:
        raise CaptureError("display must be 1 or greater.")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "display": display,
        "x": region[0],
        "y": region[1],
        "width": region[2],
        "height": region[3],
    }
    # Atomic replacement prevents a server crash from leaving half-written JSON.
    fd, temporary = tempfile.mkstemp(prefix="workspace-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return {"ok": True, "configured": True, "path": str(path), **value}


def configure_display(display: int) -> dict:
    """Explicitly dedicate one macOS display to iPad capture."""
    if display < 1:
        raise CaptureError("display must be 1 or greater.")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"mode": "display", "display": display}
    fd, temporary = tempfile.mkstemp(prefix="workspace-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return {"ok": True, "configured": True, "path": str(path), **value}
