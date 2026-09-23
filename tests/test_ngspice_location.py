"""Which ngspice runs, and what is said when there is none.

A checkout uses the one on PATH. The packaged app carries its own, because a
Mac without Homebrew has none, and names it with an environment variable the
way every other relocatable location is named.
"""
from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest

from circuit_mcp import paths
from circuit_mcp.spice import SpiceError, simulate_spice


@pytest.fixture(autouse=True)
def no_override(monkeypatch):
    monkeypatch.delenv("CIRCUIT_MCP_NGSPICE", raising=False)


def executable(path: Path, script: str = "#!/bin/sh\n") -> Path:
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_the_binary_on_path_is_the_one_a_checkout_uses():
    found = shutil.which("ngspice")
    assert paths.ngspice() == (Path(found) if found else None)


def test_the_variable_names_the_binary_when_it_is_set(tmp_path, monkeypatch):
    bundled = executable(tmp_path / "ngspice")
    monkeypatch.setenv("CIRCUIT_MCP_NGSPICE", str(bundled))
    assert paths.ngspice() == bundled


def test_a_variable_naming_nothing_executable_is_refused_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_NGSPICE", str(tmp_path / "absent"))
    with pytest.raises(ValueError, match="CIRCUIT_MCP_NGSPICE"):
        paths.ngspice()


def test_simulation_runs_the_binary_the_variable_names(tmp_path, monkeypatch):
    """Not the one on PATH: inside the app there is no ngspice on PATH at all."""
    marker = tmp_path / "ran"
    stub = executable(tmp_path / "ngspice", f"#!/bin/sh\necho ran >{marker}\nexit 9\n")
    monkeypatch.setenv("CIRCUIT_MCP_NGSPICE", str(stub))
    with pytest.raises(SpiceError, match="ngspice failed"):
        simulate_spice("V1 in 0 10\nR1 in 0 1k", "op", ["v(in)"])
    assert marker.is_file()


def test_no_ngspice_anywhere_fails_loudly_and_names_the_variable(monkeypatch):
    monkeypatch.setattr(paths.shutil, "which", lambda _: None)
    with pytest.raises(SpiceError, match="CIRCUIT_MCP_NGSPICE"):
        simulate_spice("V1 in 0 10\nR1 in 0 1k", "op", ["v(in)"])
