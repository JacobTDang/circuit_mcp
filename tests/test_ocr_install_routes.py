"""The desk's side of the installer: offer it, run it, cancel it, survive refusing it."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from circuit_mcp import ocr_install, web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_OCR_PYTHON", str(tmp_path / "ocr" / "venv" / "bin" / "python"))
    monkeypatch.setenv("CIRCUIT_MCP_OCR_MODEL", str(tmp_path / "ocr" / "models" / "unimernet_small"))
    monkeypatch.setattr(web, "OCR_INSTALLER", ocr_install.OCRInstaller(run=lambda *_: 0))
    return TestClient(web.app, headers={"host": "localhost:2300"})


def test_the_desk_is_told_what_is_missing_and_what_it_would_cost(client):
    body = client.get("/api/ocr/install").json()
    assert body["state"] == "idle"
    assert body["installed"] is False
    assert body["download_bytes"] == ocr_install.CHECKPOINT_BYTES
    assert "GB" in body["download_note"] or "MB" in body["download_note"]


def test_starting_an_install_answers_with_the_state_it_moved_to(client):
    assert client.post("/api/ocr/install").status_code == 200


def test_cancelling_when_nothing_runs_is_not_an_error(client):
    assert client.delete("/api/ocr/install").status_code == 200


def test_a_refused_start_is_a_sentence_and_not_a_stack_trace(client, monkeypatch):
    def refuse() -> None:
        raise ocr_install.OCRInstallError("The recogniser is already installed.")

    monkeypatch.setattr(web.OCR_INSTALLER, "start", refuse)
    response = client.post("/api/ocr/install")
    assert response.status_code == 409
    assert response.json()["detail"] == "The recogniser is already installed."


def test_refusing_the_install_leaves_the_rest_of_the_desk_working(client):
    """Never installing it is a legitimate choice; the tools that need no model
    must not be behind the one that does."""
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["ocr"]["ok"] is False
    assert client.get("/api/library").status_code == 200
    assert client.get("/api/problems").status_code == 200
    divider = {"netlist": "V1 in 0 10\nR1 in out 1k\nR2 out 0 2k",
               "analysis": "op", "outputs": ["v(out)"]}
    run = client.post("/api/tools/simulate_spice", json={"arguments": divider})
    assert run.status_code == 200 and run.json()["ok"] is True
