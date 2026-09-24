"""Every location the command center reads or writes resolves through circuit_mcp.paths."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from circuit_mcp import paths

VARIABLES = (
    "CIRCUIT_MCP_DATA_DIR", "CIRCUIT_MCP_SHOWMAN_DATA_DIR", "CIRCUIT_MCP_RUNTIME_DIR",
    "CIRCUIT_MCP_WORKSPACE_CONFIG", "CIRCUIT_MCP_OCR_PYTHON", "CIRCUIT_MCP_OCR_MODEL",
    "CIRCUIT_MCP_NGSPICE",
)


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_repo_defaults_are_the_locations_used_before_this_module_existed():
    root = paths.REPO_ROOT
    assert (root / "pyproject.toml").is_file()
    assert paths.data_dir() == (root / ".local" / "command_center").resolve()
    assert paths.showman_data_dir() == (root / ".local" / "showman").resolve()
    assert paths.runtime_dir() == (root / ".local" / "runtime").resolve()
    assert paths.workspace_config() == (root / ".local" / "workspace.json").resolve()
    assert paths.ocr_python() == (root / ".venv-ocr.nosync" / "bin" / "python").absolute()
    assert paths.ocr_model() == (root / "models" / "unimernet_small").resolve()


@pytest.mark.parametrize("name, location", [
    ("CIRCUIT_MCP_DATA_DIR", paths.data_dir),
    ("CIRCUIT_MCP_SHOWMAN_DATA_DIR", paths.showman_data_dir),
    ("CIRCUIT_MCP_RUNTIME_DIR", paths.runtime_dir),
    ("CIRCUIT_MCP_WORKSPACE_CONFIG", paths.workspace_config),
    ("CIRCUIT_MCP_OCR_MODEL", paths.ocr_model),
])
def test_each_location_follows_its_environment_variable(tmp_path, monkeypatch, name, location):
    monkeypatch.setenv(name, str(tmp_path / "moved"))
    assert location() == (tmp_path / "moved").resolve()


def test_an_empty_variable_is_refused_by_name(monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_DATA_DIR", "")
    with pytest.raises(ValueError, match="CIRCUIT_MCP_DATA_DIR is set but empty"):
        paths.data_dir()


def test_the_ocr_python_keeps_its_venv_symlink_unresolved(tmp_path, monkeypatch):
    base = tmp_path / "base" / "python3.12"
    base.parent.mkdir()
    base.write_text("")
    link = tmp_path / "venv" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(base)
    monkeypatch.setenv("CIRCUIT_MCP_OCR_PYTHON", str(link))
    assert paths.ocr_python() == link


def test_a_relative_ocr_python_is_taken_relative_to_the_repo(monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_OCR_PYTHON", "envs/ocr/bin/python")
    assert paths.ocr_python() == paths.REPO_ROOT / "envs" / "ocr" / "bin" / "python"


def test_the_web_layer_and_storage_both_use_the_data_folder_variable(tmp_path):
    """web.DATA is computed at import, so this runs in a fresh interpreter."""
    target = tmp_path / "elsewhere"
    env = {**os.environ, "CIRCUIT_MCP_DATA_DIR": str(target),
           "PYTHONPATH": str(paths.REPO_ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, "-c",
         "from circuit_mcp import storage, web; print(web.DATA); print(storage.default_data_dir())"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == [str(target.resolve())] * 2


def test_the_runtime_tools_follow_the_runtime_variable(tmp_path):
    env = {**os.environ, "CIRCUIT_MCP_RUNTIME_DIR": str(tmp_path / "runtime"),
           "PYTHONPATH": str(paths.REPO_ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, "-c",
         "from circuit_mcp import ipad_capture as c, paths; "
         "print(paths.uxplay() or paths.runtime_dir() / 'uxplay' / 'bin' / 'uxplay'); print(c.USB_CAPTURE)"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    runtime = (tmp_path / "runtime").resolve()
    assert completed.stdout.split() == [str(runtime / "uxplay" / "bin" / "uxplay"),
                                        str(runtime / "bin" / "ipad_usb_capture")]
