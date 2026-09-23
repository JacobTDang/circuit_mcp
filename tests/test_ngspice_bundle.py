"""The simulator the app carries must not reach back to the machine that built it.

A Mac without Homebrew has no `/opt/homebrew`, so a staged ngspice that still
names one of its dylibs is a binary that launches here and dies there. The only
answer that proves anything is to read what the staged files load.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from circuit_mcp.spice import simulate_spice

SCRIPT = Path(__file__).resolve().parents[1] / "macos" / "stage_ngspice.sh"
INSIDE = ("@rpath/", "@loader_path/", "@executable_path/", "/usr/lib/", "/System/")

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("ngspice") is None or shutil.which("otool") is None,
    reason="staging relocates Mach-O dylibs, so it needs a Mac with ngspice and the linker tools",
)


@pytest.fixture(scope="module")
def staged(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("ngspice-bundle") / "ngspice"
    run = subprocess.run([str(SCRIPT), str(destination)], capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stdout + run.stderr
    return destination


def loads(binary: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, check=True)
    return [line.split(" (")[0].strip() for line in out.stdout.splitlines()[1:]]


def test_the_staged_simulator_solves_a_divider(staged, monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_NGSPICE", str(staged / "bin" / "ngspice"))
    result = simulate_spice("V1 in 0 10\nR1 in out 1k\nR2 out 0 2k", "op", ["v(out)"])
    assert result["points"][0]["v(out)"] == pytest.approx(20 / 3, rel=1e-12)


def test_nothing_staged_loads_a_library_from_outside_the_bundle(staged):
    binaries = [staged / "bin" / "ngspice", *sorted((staged / "lib").glob("*.dylib"))]
    assert len(binaries) > 1, "the simulator links libraries that have to travel with it"
    outside = {f"{binary.name}: {load}"
               for binary in binaries for load in loads(binary)
               if not load.startswith(INSIDE)}
    assert outside == set()


def test_every_staged_binary_is_arm64(staged):
    for binary in [staged / "bin" / "ngspice", *(staged / "lib").glob("*.dylib")]:
        archs = subprocess.run(["lipo", "-archs", str(binary)], capture_output=True, text=True,
                               check=True).stdout.split()
        assert "arm64" in archs, f"{binary.name} is {archs}"


def test_staging_refuses_rather_than_leaving_an_empty_folder(tmp_path):
    run = subprocess.run([str(SCRIPT), str(tmp_path / "out")], capture_output=True, text=True,
                         timeout=60, env={"PATH": "/usr/bin:/bin", "NGSPICE": "/nonexistent/ngspice"})
    assert run.returncode != 0
    assert "ngspice" in run.stderr
