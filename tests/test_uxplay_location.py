"""Where UxPlay is found, and what the desk says when it is nowhere.

AirPlay needs a receiver this project does not build. A checkout builds one into
the runtime folder; a Mac that has it from Homebrew already has it on PATH; the
packaged app has neither until someone installs one. Each of those is a
different sentence, and "UxPlay is not built" was the only one being said.
"""
from __future__ import annotations

import plistlib
import stat
from pathlib import Path

import pytest

from circuit_mcp import paths


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture(autouse=True)
def no_override(monkeypatch):
    monkeypatch.delenv("CIRCUIT_MCP_UXPLAY", raising=False)


def test_the_variable_names_the_receiver_when_it_is_set(tmp_path, monkeypatch):
    named = executable(tmp_path / "elsewhere" / "uxplay")
    monkeypatch.setenv("CIRCUIT_MCP_UXPLAY", str(named))
    assert paths.uxplay() == named


def test_the_runtime_build_is_preferred_over_one_on_path(tmp_path, monkeypatch):
    built = executable(tmp_path / "runtime" / "uxplay" / "bin" / "uxplay")
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(paths.shutil, "which", lambda _: "/opt/homebrew/bin/uxplay")
    assert paths.uxplay() == built


def test_one_on_path_is_used_when_nothing_was_built(tmp_path, monkeypatch):
    installed = executable(tmp_path / "brew" / "uxplay")
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(paths.shutil, "which", lambda _: str(installed))
    assert paths.uxplay() == installed


def test_no_receiver_anywhere_is_none_rather_than_a_path_that_is_not_there(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(paths.shutil, "which", lambda _: None)
    assert paths.uxplay() is None


def test_the_status_says_what_is_missing_and_how_to_get_it(tmp_path, monkeypatch):
    from circuit_mcp.ipad_capture import IPadCaptureService

    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(paths.shutil, "which", lambda _: None)
    airplay = IPadCaptureService().status()["airplay"]
    assert airplay["available"] is False
    unavailable = airplay["unavailable"]
    assert "uxplay" in unavailable.lower()
    assert "setup_ipad_capture.sh" in unavailable
    # There is no uxplay formula in Homebrew core: `brew install uxplay` answers
    # "No available formula", so naming it sends the student to a dead end.
    assert "brew install uxplay" not in unavailable


def test_a_receiver_that_is_there_reports_no_reason(tmp_path, monkeypatch):
    from circuit_mcp.ipad_capture import IPadCaptureService

    monkeypatch.setenv("CIRCUIT_MCP_UXPLAY", str(executable(tmp_path / "uxplay")))
    airplay = IPadCaptureService().status()["airplay"]
    assert airplay["available"] is True
    assert airplay["unavailable"] == ""


def test_the_app_declares_why_it_wants_the_local_network(): 
    """UxPlay advertises the receiver over mDNS, which macOS gates behind Local
    Network permission. Without the purpose string the app is refused silently
    and the iPad simply never sees a receiver to mirror to."""
    plist = plistlib.loads(
        (paths.REPO_ROOT / "macos" / "Resources" / "Info.plist").read_bytes())
    assert "AirPlay" in plist["NSLocalNetworkUsageDescription"]
    assert set(plist["NSBonjourServices"]) >= {"_airplay._tcp", "_raop._tcp"}


def test_a_homebrew_receiver_is_found_although_the_app_bounds_its_path(tmp_path, monkeypatch):
    """The app starts the server with PATH=/usr/bin:/bin:/usr/sbin:/sbin on purpose.

    shutil.which would therefore never see /opt/homebrew/bin, so the status told
    the student to run `brew install uxplay` and the app still would not find it.
    The well-known locations are checked by name instead of widening that PATH.
    """
    installed = executable(tmp_path / "opt" / "homebrew" / "bin" / "uxplay")
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(paths, "HOMEBREW_PREFIXES", (tmp_path / "opt" / "homebrew",))
    monkeypatch.setattr(paths.shutil, "which", lambda _: None)
    assert paths.uxplay() == installed
