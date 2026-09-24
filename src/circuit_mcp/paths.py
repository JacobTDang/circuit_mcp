"""Every location the command center reads or writes outside its own package.

Running from a checkout keeps the historical defaults under the repository. The
macOS app, and anyone else who needs to relocate state, sets the matching
environment variable. Each function reads its variable when called; modules that
turn a location into a constant at import time must be imported after the
variable is set.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The app starts its server with PATH=/usr/bin:/bin:/usr/sbin:/sbin on purpose, so
# shutil.which can never see a Homebrew install. Tools the student is told to brew
# install are looked for by name here instead of widening that PATH for everything.
HOMEBREW_PREFIXES = (Path("/opt/homebrew"), Path("/usr/local"))


def _override(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None and not value.strip():
        raise ValueError(f"{name} is set but empty")
    return value


def _location(name: str, default: Path) -> Path:
    value = _override(name)
    return (Path(value).expanduser() if value else default).resolve()


def data_dir() -> Path:
    return _location("CIRCUIT_MCP_DATA_DIR", REPO_ROOT / ".local" / "command_center")


def showman_root() -> Path:
    """The vendored Showman checkout. The packaged app names one or carries none."""
    return _location("CIRCUIT_MCP_SHOWMAN_ROOT", REPO_ROOT / "vendor" / "showman")


def showman_data_dir() -> Path:
    return _location("CIRCUIT_MCP_SHOWMAN_DATA_DIR", REPO_ROOT / ".local" / "showman")


def runtime_dir() -> Path:
    return _location("CIRCUIT_MCP_RUNTIME_DIR", REPO_ROOT / ".local" / "runtime")


def workspace_config() -> Path:
    return _location("CIRCUIT_MCP_WORKSPACE_CONFIG", REPO_ROOT / ".local" / "workspace.json")


def ocr_model() -> Path:
    return _location("CIRCUIT_MCP_OCR_MODEL", REPO_ROOT / "models" / "unimernet_small")


def ocr_python() -> Path:
    value = _override("CIRCUIT_MCP_OCR_PYTHON")
    python = Path(value).expanduser() if value else REPO_ROOT / ".venv-ocr.nosync" / "bin" / "python"
    if not python.is_absolute():
        python = REPO_ROOT / python
    # Never resolve: a venv's python is a symlink to its base interpreter, and
    # resolving it would discard the venv's site-packages.
    return python.absolute()


def ngspice() -> Path | None:
    """The simulator binary: the one the app carries, else the one on PATH.

    Unset is not a failure -- a checkout uses Homebrew's -- so this returns None
    and the caller says so. A variable that names something unrunnable is a
    failure: the app sets it deliberately, and falling back to PATH there would
    let a broken bundle pass for a working one on the developer's own machine.
    """
    value = _override("CIRCUIT_MCP_NGSPICE")
    if value:
        binary = Path(value).expanduser().absolute()
        if not (binary.is_file() and os.access(binary, os.X_OK)):
            raise ValueError(f"CIRCUIT_MCP_NGSPICE names {binary}, which is not an executable file")
        return binary
    found = shutil.which("ngspice")
    return Path(found) if found else None


def uxplay() -> Path | None:
    """The AirPlay receiver: the one named, else the one built, else one on PATH.

    This project does not build UxPlay, so all three are possible and they are
    different situations for the person reading the screen. None means there is
    genuinely none, which the status turns into a sentence rather than a path
    that is not there.
    """
    value = _override("CIRCUIT_MCP_UXPLAY")
    if value:
        binary = Path(value).expanduser().absolute()
        if not (binary.is_file() and os.access(binary, os.X_OK)):
            raise ValueError(f"CIRCUIT_MCP_UXPLAY names {binary}, which is not an executable file")
        return binary
    candidates = [runtime_dir() / "uxplay" / "bin" / "uxplay"]
    found = shutil.which("uxplay")
    if found:
        candidates.append(Path(found))
    candidates.extend(prefix / "bin" / "uxplay" for prefix in HOMEBREW_PREFIXES)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None
