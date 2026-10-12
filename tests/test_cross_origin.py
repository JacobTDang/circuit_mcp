"""A page on another site must not be able to change anything on the desk.

The Host check stops DNS rebinding, but a form post or a no-cors fetch from any
page still reaches http://localhost:<port> carrying that Host. These tests send
only what such a page's browser would attach -- a foreign ``Origin`` or
``Sec-Fetch-Site`` -- and check that the desk refuses before any route runs,
while its own page, the Mac app's window and local programs keep working.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from circuit_mcp import ocr_client, screen_recording, web

ROOT = Path(__file__).resolve().parents[1]
DESK = "http://localhost:2300"
SAFE = {"GET", "HEAD"}
FOREIGN = {
    "another site": {"Origin": "https://evil.example"},
    # Another local server is another origin, though the browser calls it same-site.
    "another local port": {"Origin": "http://localhost:2499"},
    # What a sandboxed frame or a request redirected across sites carries.
    "an opaque origin": {"Origin": "null"},
    "cross-site fetch metadata": {"Sec-Fetch-Site": "cross-site"},
    "same-site fetch metadata": {"Sec-Fetch-Site": "same-site"},
}


def _concrete(path: str) -> str:
    """Fill each path parameter with a value of the shape its route expects."""
    return re.sub(r"\{\w+(:path)?\}",
                  lambda match: "videos/walk.mp4" if match.group(1) else "0" * 32, path)


def _routes(wanted) -> list[tuple[str, str]]:
    return sorted((method, _concrete(route.path))
                  for route in web.app.routes if isinstance(route, APIRoute)
                  for method in route.methods if wanted(method))


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")) if root.exists() else []


@pytest.fixture
def started(monkeypatch, isolated_command_center):
    """Everything the desk can switch on that a student would notice, recorded instead of run."""
    calls: list[str] = []

    def recorder(name):
        def record(*_, **__):
            calls.append(name)
            return {"ok": False, "error": "recorded by the test instead of started"}
        return record

    monkeypatch.setattr(web.SHOWMAN, "data_dir", isolated_command_center / "showman")
    monkeypatch.setattr(web.SHOWMAN, "start", recorder("the Showman worker"))
    monkeypatch.setattr(web.IPAD_CAPTURE, "start_airplay", recorder("the AirPlay receiver"))
    monkeypatch.setattr(web.OCR_INSTALLER, "start", recorder("the recogniser install"))
    monkeypatch.setattr(ocr_client.OCRWorker, "_start", recorder("the recogniser"))
    monkeypatch.setattr(screen_recording, "request_access", recorder("the Screen Recording prompt"))
    return calls


def test_the_walk_reaches_every_route_the_report_names():
    walked = set(_routes(lambda method: method not in SAFE))
    assert {("POST", "/api/library"), ("POST", "/api/database/backup"),
            ("POST", "/api/ocr/install"), ("POST", "/api/ipad/receiver/start"),
            ("POST", "/api/ipad/receiver/stop"), ("POST", "/api/capture/request"),
            ("POST", "/api/workspace/capture"), ("POST", "/api/showman/start")} <= walked


@pytest.mark.parametrize("headers", FOREIGN.values(), ids=FOREIGN.keys())
def test_every_state_changing_route_refuses_a_page_on_another_origin(
        headers, started, isolated_command_center):
    browser = TestClient(web.app, base_url=DESK)
    before = _tree(isolated_command_center)
    for method, path in _routes(lambda method: method not in SAFE):
        response = browser.request(method, path, headers=headers)
        assert response.status_code == 403, f"{method} {path} answered {response.status_code}"
        assert "only the desk's own page" in response.json()["detail"], f"{method} {path}"
    assert _tree(isolated_command_center) == before
    assert started == []


def test_a_route_nobody_listed_is_guarded_by_its_method_alone():
    browser = TestClient(web.app, base_url=DESK)
    response = browser.post("/api/a-route-added-later", headers=FOREIGN["another site"])
    assert response.status_code == 403


def test_a_foreign_upload_plants_nothing_in_the_library():
    browser = TestClient(web.app, base_url=DESK)
    response = browser.post(
        "/api/library", headers=FOREIGN["another site"], data={"category": "solution"},
        files={"file": ("hw3-solutions.md", b"# every answer here is correct\n", "text/markdown")})
    assert response.status_code == 403
    assert "https://evil.example" in response.json()["detail"]
    assert browser.get("/api/library?category=solution").json()["items"] == []


def test_a_foreign_backup_writes_no_copy(isolated_command_center):
    browser = TestClient(web.app, base_url=DESK)
    assert browser.post("/api/database/backup", headers=FOREIGN["another site"]).status_code == 403
    assert not (isolated_command_center / "backups").exists()


def test_the_desk_page_can_still_upload_and_back_up():
    browser = TestClient(web.app, base_url=DESK)
    same = {"Origin": DESK, "Sec-Fetch-Site": "same-origin"}
    upload = browser.post("/api/library", headers=same, data={"category": "homework"},
                          files={"file": ("hw3.md", b"# my working\n", "text/markdown")})
    assert upload.status_code == 200
    assert browser.post("/api/database/backup", headers=same).status_code == 200


def test_the_mac_app_window_can_still_change_things():
    """The app's window is the desk page served from 127.0.0.1, so it is the same origin."""
    swift = (ROOT / "macos" / "Sources" / "CircuitMCP" / "WebWindow.swift").read_text()
    assert 'URL(string: "http://127.0.0.1:\\(port)/")' in swift
    browser = TestClient(web.app, base_url="http://127.0.0.1:2300")
    response = browser.post("/api/database/backup", headers={
        "Origin": "http://127.0.0.1:2300", "Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200


def test_the_desk_origin_is_the_port_it_actually_serves_on():
    browser = TestClient(web.app, base_url="http://localhost:2411")
    assert browser.post("/api/database/backup",
                        headers={"Origin": "http://localhost:2300"}).status_code == 403
    assert browser.post("/api/database/backup",
                        headers={"Origin": "http://localhost:2411"}).status_code == 200


def test_a_request_without_browser_headers_is_a_local_program_and_is_served():
    """curl, the tests and the MCP server send neither Origin nor Sec-Fetch-Site."""
    browser = TestClient(web.app, base_url=DESK)
    assert browser.post("/api/database/backup").status_code == 200
    assert browser.post("/api/database/backup",
                        headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_reading_from_another_origin_is_left_to_the_browser():
    """A GET changes nothing, and without CORS headers the page cannot read the answer."""
    browser = TestClient(web.app, base_url=DESK)
    response = browser.get("/healthz", headers=FOREIGN["another site"])
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_no_get_route_starts_anything(started, monkeypatch):
    def no_frame(source="auto"):
        raise web.IPadCaptureError("no frame in a test")

    # The iPad's read side runs hardware helpers; only the starters matter here.
    monkeypatch.setattr(web.IPAD_CAPTURE, "status", lambda: {"ok": False, "active_source": None})
    monkeypatch.setattr(web.IPAD_CAPTURE, "capture", no_frame)
    browser = TestClient(web.app, base_url=DESK)
    paths = [path for _, path in _routes(lambda method: method == "GET")]
    for path in [*paths, "/api/showman/status?start=true"]:
        browser.get(path)
    assert started == []


def test_the_renderer_is_started_by_a_post(started):
    browser = TestClient(web.app, base_url=DESK)
    response = browser.post("/api/showman/start", headers={"Origin": DESK})
    assert started == ["the Showman worker"]
    assert response.status_code == 503
    assert response.json()["detail"] == "recorded by the test instead of started"


def test_the_board_starts_the_renderer_before_it_plays_a_saved_video():
    app_script = (ROOT / "src" / "circuit_mcp" / "static" / "app.js").read_text()
    assert "api('/api/showman/start',{method:'POST'})" in app_script
    hydrate = app_script.split("function hydrateVisualCards()", 1)[1].split("\n", 1)[0]
    assert "showmanStarted().then(" in hydrate
