"""Retention for rendered artifacts (issue #22).

Bytes and references expire together: a swept object must never leave a row
behind that still advertises its URL.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from circuit_mcp.retention import RETENTION_SECONDS, sweep
from circuit_mcp.storage import CommandCenterDB


def _db(tmp_path) -> CommandCenterDB:
    database = CommandCenterDB(tmp_path / "circuit_mcp.sqlite3")
    database.prepare(None, None)
    return database


def _objects(tmp_path) -> Path:
    directory = tmp_path / "objects" / "videos"
    directory.mkdir(parents=True)
    return tmp_path / "objects"


def _write(objects: Path, key: str, age_seconds: float = 0) -> Path:
    path = objects / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"artifact")
    if age_seconds:
        stamp = time.time() - age_seconds
        import os
        os.utime(path, (stamp, stamp))
    return path


def _render(key: str) -> dict:
    return {"video": {"key": key}, "durationSec": 10, "fps": 30, "spec": {"specVersion": 1}}


OLD = RETENTION_SECONDS + 600


def test_an_unlinked_visual_past_the_window_expires_with_its_bytes(tmp_path):
    database, objects = _db(tmp_path), _objects(tmp_path)
    visual = database.create_visual("ad-hoc brief", _render("videos/a.mp4"))
    database.age_visual_for_test(visual["id"], time.time() - OLD)
    video = _write(objects, "videos/a.mp4", OLD)

    result = sweep(database, objects)

    assert not video.exists()
    assert database.list_visuals() == []
    assert result["expired_visuals"] == 1 and result["deleted_objects"] == 1


def test_an_unlinked_visual_inside_the_window_survives(tmp_path):
    database, objects = _db(tmp_path), _objects(tmp_path)
    database.create_visual("recent brief", _render("videos/a.mp4"))
    video = _write(objects, "videos/a.mp4")

    sweep(database, objects)

    assert video.exists() and len(database.list_visuals()) == 1


def test_a_visual_linked_to_a_problem_is_never_expired_by_the_clock(tmp_path):
    database, objects = _db(tmp_path), _objects(tmp_path)
    problem = database.create_problem(title="RC", topic="transients", prompt="find tau")
    visual = database.create_visual("linked brief", _render("videos/a.mp4"), problem_id=problem["id"])
    database.age_visual_for_test(visual["id"], time.time() - OLD * 10)
    video = _write(objects, "videos/a.mp4", OLD * 10)

    sweep(database, objects)

    assert video.exists(), "work attached to a problem cost something; the clock must not take it"
    assert len(database.list_visuals()) == 1


def test_captions_and_the_spec_beside_a_kept_video_survive(tmp_path):
    database, objects = _db(tmp_path), _objects(tmp_path)
    problem = database.create_problem(title="RC", topic="transients", prompt="find tau")
    database.create_visual("linked", _render("videos/a.mp4"), problem_id=problem["id"])
    kept = [_write(objects, key, OLD) for key in
            ("videos/a.mp4", "videos/a.vtt", "videos/a.srt")]

    sweep(database, objects)

    assert all(path.exists() for path in kept)


def test_an_unreferenced_object_inside_the_grace_window_is_left_alone(tmp_path):
    """A render in flight has no row yet; sweeping it would delete live work."""
    database, objects = _db(tmp_path), _objects(tmp_path)
    fresh = _write(objects, "videos/in-flight.mp4")

    sweep(database, objects)

    assert fresh.exists()


def test_an_unreferenced_object_past_the_window_is_deleted(tmp_path):
    database, objects = _db(tmp_path), _objects(tmp_path)
    orphan = _write(objects, "videos/orphan.mp4", OLD)

    result = sweep(database, objects)

    assert not orphan.exists() and result["deleted_objects"] == 1


def test_listing_never_advertises_a_url_whose_bytes_are_gone(tmp_path):
    database, objects = _db(tmp_path), _objects(tmp_path)
    visual = database.create_visual("ad-hoc", _render("videos/a.mp4"))
    database.age_visual_for_test(visual["id"], time.time() - OLD)
    _write(objects, "videos/a.mp4", OLD)

    sweep(database, objects)

    for item in database.list_visuals():
        assert (objects / item["object_key"]).exists()


def test_a_missing_object_directory_is_not_an_error(tmp_path):
    database = _db(tmp_path)
    assert sweep(database, tmp_path / "absent")["deleted_objects"] == 0


def test_the_sweep_cannot_delete_outside_the_object_directory(tmp_path):
    database, objects = _db(tmp_path), _objects(tmp_path)
    outside = tmp_path / "precious.mp4"
    outside.write_bytes(b"not ours")
    import os
    os.utime(outside, (time.time() - OLD, time.time() - OLD))
    (objects / "escape.mp4").symlink_to(outside)

    sweep(database, objects)

    assert outside.exists(), "a symlink must not lead the sweep out of the store"


def test_the_object_proxy_reports_a_missing_artifact_as_gone(monkeypatch):
    from fastapi.testclient import TestClient

    from circuit_mcp import web
    from circuit_mcp.showman import ShowmanMissingObject

    def missing(key, timeout=30):
        raise ShowmanMissingObject(f"no artifact for {key}")

    monkeypatch.setattr(web.SHOWMAN, "object_bytes", missing)
    client = TestClient(web.app, headers={"host": "localhost:2300"})
    assert client.get("/api/showman/objects/videos/gone.mp4").status_code == 404


def test_a_render_started_from_the_browser_is_tracked_not_orphaned(tmp_path, monkeypatch):
    """An untracked render looks like an orphan and would be swept."""
    from fastapi.testclient import TestClient

    from circuit_mcp import web

    database = _db(tmp_path)
    monkeypatch.setattr(web, "_db", lambda: database)
    monkeypatch.setattr(web.SHOWMAN, "start", lambda *a, **k: {"ok": True, "authoring": "openrouter"})
    monkeypatch.setattr(web.SHOWMAN, "request_json",
                        lambda path, payload, timeout: (200, _render("videos/browser.mp4")))
    client = TestClient(web.app, headers={"host": "localhost:2300"})

    body = client.post("/api/showman/generate", json={"brief": "explain RC charging"}).json()

    assert body["videoUrl"] == "/api/showman/objects/videos/browser.mp4"
    assert [v["object_key"] for v in database.list_visuals()] == ["videos/browser.mp4"]
    assert body["visualId"] == database.list_visuals()[0]["id"]
