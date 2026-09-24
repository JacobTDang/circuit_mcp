"""Installing the recogniser from inside the app.

Transcription needs an 810 MB checkpoint and its own virtualenv, installed
until now by running scripts/setup_ocr.sh in a terminal. Someone who opens an
app has no terminal in the story, and the script needs uv, which the app does
not carry and could not reach anyway: the server's PATH is bounded on purpose.

Two things decide whether this is safe to offer. Nothing may move into place
until every step has succeeded, so a failure halfway leaves the desk exactly as
it was; and whatever a failure or a cancellation leaves behind has to be removed
and named, because two gigabytes of half-written virtualenv is not something to
discover later.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from circuit_mcp import ocr_install
from circuit_mcp.ocr_install import OCRInstaller

CHECKPOINT = "unimernet_small.pth"


@pytest.fixture
def destination(tmp_path, monkeypatch):
    """Point the install at a throwaway tree, as the app points it at its own."""
    monkeypatch.setenv("CIRCUIT_MCP_OCR_PYTHON", str(tmp_path / "ocr" / "venv" / "bin" / "python"))
    monkeypatch.setenv("CIRCUIT_MCP_OCR_MODEL", str(tmp_path / "ocr" / "models" / "unimernet_small"))
    return tmp_path / "ocr"


def settle(installer: OCRInstaller, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while installer.state()["state"] == "running":
        assert time.monotonic() < deadline, "the install never finished"
        time.sleep(0.01)
    return installer.state()


def fake_install(destination: Path):
    """Write what a real run writes, so the move into place is the real move."""
    def run(step, staging):
        if step.label == "environment":
            (staging / "venv" / "bin").mkdir(parents=True)
            (staging / "venv" / "bin" / "python").write_text("#!/bin/sh\n")
            (staging / "venv" / "bin" / "python").chmod(0o755)
        elif step.label == "model":
            (staging / "model").mkdir(parents=True)
            (staging / "model" / CHECKPOINT).write_bytes(b"weights")
        return 0
    return run


def test_the_offer_states_the_size_before_anything_is_downloaded(destination):
    state = OCRInstaller().state()
    assert state["state"] == "idle"
    assert state["download_bytes"] == ocr_install.CHECKPOINT_BYTES
    assert "MB" in state["download_size"] or "GB" in state["download_size"]
    assert not destination.exists(), "asking what it would cost must download nothing"


def test_the_environment_is_built_with_this_interpreter_because_there_is_no_uv(destination):
    steps = {step.label: step.argv for step in OCRInstaller().plan(Path("/staging"))}
    assert steps["environment"][1:3] == ["-m", "venv"]
    assert "uv" not in " ".join(steps["environment"])
    assert "unimernet==0.2.3" in steps["packages"]
    assert ocr_install.MODEL_REPOSITORY in steps["model"]


def test_a_finished_install_is_reported_without_restarting_anything(destination):
    from circuit_mcp.ocr_client import OCRWorker

    assert OCRWorker().availability()["ok"] is False
    installer = OCRInstaller(run=fake_install(destination))
    installer.start()
    assert settle(installer)["state"] == "done"
    assert OCRWorker().availability()["ok"] is True


def test_nothing_moves_into_place_until_every_step_has_succeeded(destination):
    def fail_at_the_model(step, staging):
        if step.label == "model":
            return 1
        return fake_install(destination)(step, staging)

    installer = OCRInstaller(run=fail_at_the_model)
    installer.start()
    assert settle(installer)["state"] == "failed"
    assert not (destination / "venv").exists(), "a venv landed although the model never arrived"
    assert not (destination / "models").exists()


def test_a_failed_install_removes_its_staging_and_says_what_it_removed(destination):
    def fail_at_the_model(step, staging):
        if step.label == "model":
            return 1
        return fake_install(destination)(step, staging)

    installer = OCRInstaller(run=fail_at_the_model)
    installer.start()
    state = settle(installer)
    assert state["removed"], "a failure that cleans up silently is not reportable"
    assert not any(Path(path).exists() for path in state["removed"])


def test_a_cancelled_install_removes_its_staging_and_says_what_it_removed(destination):
    installer = OCRInstaller(run=None)

    def slow(step, staging):
        fake_install(destination)(step, staging)
        while not installer.cancelled:
            time.sleep(0.01)
        return 1

    installer._run = slow
    installer.start()
    while installer.state()["step"] != "environment":
        time.sleep(0.01)
    installer.cancel()
    state = settle(installer)
    assert state["state"] == "cancelled"
    assert state["removed"]
    assert not any(Path(path).exists() for path in state["removed"])
    assert not (destination / "venv").exists()


def test_a_second_start_is_refused_rather_than_racing_the_first(destination):
    installer = OCRInstaller(run=None)

    def slow(step, staging):
        while not installer.cancelled:
            time.sleep(0.01)
        return 1

    installer._run = slow
    installer.start()
    with pytest.raises(ocr_install.OCRInstallError, match="already"):
        installer.start()
    installer.cancel()
    settle(installer)


def test_an_install_is_refused_when_one_is_already_there(destination):
    installer = OCRInstaller(run=fake_install(destination))
    installer.start()
    settle(installer)
    with pytest.raises(ocr_install.OCRInstallError, match="already installed"):
        installer.start()


def test_a_successful_install_reports_nothing_removed(destination):
    """`removed` is what the student lost, not the installer's own housekeeping."""
    installer = OCRInstaller(run=fake_install(destination))
    installer.start()
    state = settle(installer)
    assert state["state"] == "done"
    assert state["removed"] == []
