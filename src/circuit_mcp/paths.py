"""Every location the command center reads or writes outside its own package.

Running from a checkout keeps the historical defaults under the repository. The
macOS app, and anyone else who needs to relocate state, sets the matching
environment variable. Each function reads its variable when called; modules that
turn a location into a constant at import time must be imported after the
variable is set.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


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
