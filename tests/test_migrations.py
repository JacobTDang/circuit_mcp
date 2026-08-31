"""The forward-only schema migration path, and the step that retires animations.

``prepare()`` runs on every request, so a migration defect does not fail once --
it fails continuously against a database holding real coursework. These tests
pin the two properties that makes it safe to run there: a step runs at most
once, and the only irreversible step refuses to proceed unless its archive can
be read back off disk first.
"""
from __future__ import annotations

import json

import pytest

from circuit_mcp import storage
from circuit_mcp.storage import SCHEMA_VERSION, CommandCenterDB, StorageError

LEGACY_DDL = """
CREATE TABLE animation_scenes (
    id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id),
    problem_id TEXT REFERENCES problems(id) ON DELETE SET NULL,
    title TEXT NOT NULL, scene_json TEXT NOT NULL,
    revision INTEGER NOT NULL, created_at REAL NOT NULL,
    updated_at REAL NOT NULL, deleted_at REAL
);
CREATE INDEX animation_scenes_updated ON animation_scenes(course_id, updated_at DESC);
"""

SCENE = {"title": "RC charging in motion",
         "elements": [{"type": "resistor", "id": "R1", "x": 120, "y": 40, "angle": 0}]}


def legacy_database(tmp_path, scenes=1):
    """A store as it stood before the drop: schema v1, hand-authored scenes."""
    data = tmp_path / "command_center"
    db = CommandCenterDB(data / "circuit_mcp.sqlite3")
    db.prepare()
    with db.transaction() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version>1")
        for statement in LEGACY_DDL.strip().split(";"):
            if statement.strip():
                connection.execute(statement)
        for index in range(scenes):
            connection.execute(
                "INSERT INTO animation_scenes VALUES(?,?,?,?,?,?,?,?,NULL)",
                (f"{index:032x}", "circuits", None, SCENE["title"],
                 json.dumps(SCENE, separators=(",", ":")), 4, 100.0 + index, 200.0 + index),
            )
    return db, data


def table_exists(db, name):
    with db._connect() as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None


def applied_versions(db):
    with db._connect() as connection:
        return sorted(row[0] for row in connection.execute("SELECT version FROM schema_migrations"))


def archives(data):
    return sorted((data / "archive").glob("animation_scenes-*.json"))


def test_a_fresh_store_never_creates_the_retired_table(tmp_path):
    data = tmp_path / "command_center"
    db = CommandCenterDB(data / "circuit_mcp.sqlite3")
    report = db.prepare()
    assert report["schema_version"] == SCHEMA_VERSION
    assert not table_exists(db, "animation_scenes")
    assert applied_versions(db) == [1, 2]
    assert not archives(data)


def test_prepare_archives_then_drops_a_legacy_animation_table(tmp_path):
    db, data = legacy_database(tmp_path)
    assert table_exists(db, "animation_scenes")
    db.prepare()
    assert not table_exists(db, "animation_scenes")
    assert applied_versions(db) == [1, 2]
    assert len(archives(data)) == 1


def test_the_archive_is_a_faithful_copy_of_every_scene(tmp_path):
    db, data = legacy_database(tmp_path, scenes=3)
    db.prepare()
    stored = json.loads(archives(data)[0].read_text())
    assert stored["table"] == "animation_scenes"
    assert [row["id"] for row in stored["rows"]] == [f"{index:032x}" for index in range(3)]
    assert json.loads(stored["rows"][0]["scene_json"]) == SCENE
    assert stored["rows"][0]["revision"] == 4


def test_repeated_prepare_neither_re_archives_nor_fails(tmp_path):
    db, data = legacy_database(tmp_path)
    db.prepare()
    first = archives(data)
    db.prepare()
    db.prepare()
    assert archives(data) == first
    assert applied_versions(db) == [1, 2]


def test_an_unverifiable_archive_leaves_the_table_and_its_rows_intact(tmp_path, monkeypatch):
    db, data = legacy_database(tmp_path)

    def truncated(destination, table, rows):
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        partial.write_text(json.dumps({"table": table, "rows": rows[:-1]}))
        return partial

    monkeypatch.setattr(storage, "_archive_rows", truncated)
    with pytest.raises(StorageError, match="animation_scenes"):
        db.prepare()
    assert table_exists(db, "animation_scenes")
    with db._connect() as connection:
        assert connection.execute("SELECT count(*) FROM animation_scenes").fetchone()[0] == 1
    assert applied_versions(db) == [1]
    assert not archives(data), "a copy that failed verification must not be named like an archive"


def test_a_step_that_fails_is_retried_and_completes_once_repaired(tmp_path, monkeypatch):
    db, data = legacy_database(tmp_path)
    monkeypatch.setattr(storage, "_archive_rows",
                        lambda destination, table, rows: destination.with_name(destination.name + ".missing"))
    with pytest.raises(StorageError):
        db.prepare()
    monkeypatch.undo()
    db.prepare()
    assert not table_exists(db, "animation_scenes")
    assert applied_versions(db) == [1, 2]
    assert len(archives(data)) == 1
