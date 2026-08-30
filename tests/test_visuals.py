"""Persistence and MCP surface for Showman-rendered visuals (issue #13)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_mcp.storage import CommandCenterDB, StorageError


def _db(tmp_path) -> CommandCenterDB:
    database = CommandCenterDB(tmp_path / "circuit_mcp.sqlite3")
    database.prepare(None, None)
    return database


RENDER = {
    "video": {"key": "videos/abc.mp4"}, "durationSec": 20.0, "fps": 30,
    "width": 960, "height": 540, "spec": {"specVersion": 1, "nodes": []},
}


def test_a_rendered_visual_is_persisted_and_readable(tmp_path):
    database = _db(tmp_path)
    stored = database.create_visual("explain RC charging", RENDER)
    assert stored["brief"] == "explain RC charging"
    assert stored["object_key"] == "videos/abc.mp4"
    assert stored["url"] == "/api/showman/objects/videos/abc.mp4"
    assert stored["duration_sec"] == 20.0
    assert database.get_visual(stored["id"])["id"] == stored["id"]


def test_a_visual_can_be_linked_to_a_confirmed_problem(tmp_path):
    database = _db(tmp_path)
    problem = database.create_problem(title="RC", topic="transients", prompt="find tau")
    stored = database.create_visual("explain RC charging", RENDER, problem_id=problem["id"])
    assert stored["problem_id"] == problem["id"]
    assert [v["id"] for v in database.list_visuals(problem_id=problem["id"])] == [stored["id"]]


def test_a_visual_for_an_unknown_problem_is_refused(tmp_path):
    database = _db(tmp_path)
    with pytest.raises(StorageError):
        database.create_visual("brief", RENDER, problem_id="0" * 32)


def test_an_unknown_visual_is_not_found(tmp_path):
    database = _db(tmp_path)
    with pytest.raises(StorageError):
        database.get_visual("0" * 32)
    with pytest.raises(StorageError):
        database.get_visual("not-a-uuid")


def test_a_render_without_an_object_key_is_refused(tmp_path):
    """A visual row must point at a real artifact; a keyless render is a failed render."""
    database = _db(tmp_path)
    with pytest.raises(StorageError):
        database.create_visual("brief", {"durationSec": 5})


def test_the_authored_specification_is_kept_so_a_frame_can_be_previewed(tmp_path):
    database = _db(tmp_path)
    stored = database.create_visual("explain RC charging", RENDER)
    assert database.get_visual(stored["id"])["spec"] == RENDER["spec"]


def test_visuals_are_listed_newest_first_within_a_bound(tmp_path):
    database = _db(tmp_path)
    made = [database.create_visual(f"brief {n}", RENDER)["id"] for n in range(3)]
    listed = [v["id"] for v in database.list_visuals(limit=2)]
    assert listed == made[::-1][:2]


# --- MCP surface --------------------------------------------------------------


def test_the_legacy_animation_tools_are_gone(tmp_path):
    from circuit_mcp import server

    for name in ("animation_create", "animation_list", "animation_update",
                 "animation_delete", "animation_from_template", "animation_list_templates"):
        assert not hasattr(server, name), f"{name} drives a renderer that no longer exists"


def test_the_legacy_animation_engine_is_gone():
    with pytest.raises(ModuleNotFoundError):
        __import__("circuit_mcp.animation_engine")


def test_the_legacy_animation_routes_are_gone():
    from circuit_mcp import web

    paths = {route.path for route in web.app.routes}
    assert not any(path.startswith("/api/animations") for path in paths)


def test_visual_tools_do_not_run_under_the_lcapy_timeout(tmp_path):
    """Rendering takes 12-16s against a 20s guarded bound, and touches no lcapy state."""
    import inspect

    from circuit_mcp import server

    for name in ("visual_generate", "visual_preview"):
        assert "_guarded" not in inspect.getsource(getattr(server, name))


def test_visual_generate_refuses_when_the_worker_cannot_author(monkeypatch):
    from circuit_mcp import server

    monkeypatch.setattr(server.SHOWMAN, "start", lambda *a, **k: {"ok": True, "authoring": "offline"})
    result = server.visual_generate("explain an RC circuit")
    assert result["ok"] is False
    assert "OPENROUTER_API_KEY" in result["message"]


def test_visual_generate_persists_and_returns_a_local_url(tmp_path, monkeypatch):
    from circuit_mcp import server

    monkeypatch.setattr(server, "_storage", lambda: _db(tmp_path))
    monkeypatch.setattr(server.SHOWMAN, "start", lambda *a, **k: {"ok": True, "authoring": "openrouter"})
    monkeypatch.setattr(server.SHOWMAN, "request_json", lambda path, payload, timeout: (200, RENDER))
    result = server.visual_generate("explain RC charging")
    assert result["ok"] is True
    assert result["visual"]["url"] == "/api/showman/objects/videos/abc.mp4"
    assert "file://" not in json.dumps(result)


def test_visual_generate_reports_a_failed_render_without_persisting(tmp_path, monkeypatch):
    from circuit_mcp import server

    database = _db(tmp_path)
    monkeypatch.setattr(server, "_storage", lambda: database)
    monkeypatch.setattr(server.SHOWMAN, "start", lambda *a, **k: {"ok": True, "authoring": "openrouter"})
    monkeypatch.setattr(server.SHOWMAN, "request_json",
                        lambda path, payload, timeout: (422, {"error": "authoring_failed", "attempts": 3}))
    result = server.visual_generate("explain RC charging")
    assert result["ok"] is False
    assert "authoring_failed" in result["message"]
    assert database.list_visuals() == [], "a failed render must not leave a row behind"
