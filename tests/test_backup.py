"""Backups asked for at the same moment must each finish as one whole file.

The store runs in WAL mode. Copies were named by the second and shared one
temporary file, so a second backup deleted the first one's while SQLite still
had it open and mapped: two at once answered a bare 500, and six at once killed
the desk with SIGBUS, leaving .temporary, -wal and -shm files behind.
"""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from circuit_mcp import web
from circuit_mcp.storage import CommandCenterDB

DESK = "http://localhost:2300"
BUSY = "A backup is already running. Ask again once it has finished."


def _store(tmp_path: Path) -> CommandCenterDB:
    database = CommandCenterDB(tmp_path / "data" / "circuit_mcp.sqlite3")
    database.prepare()
    database.record_event("upload", "hw3.md", True)
    with closing(database._connect()) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    return database


def _leftovers(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir() if path.suffix != ".sqlite3")


def _assert_whole(path: Path) -> None:
    """Open it as a restore would trust it: no -wal, no -shm, nothing written."""
    with closing(sqlite3.connect(f"file:{path}?immutable=1", uri=True)) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # Bytes 18 and 19 are 2 in WAL mode, whose copy is not whole without its -wal.
    assert path.read_bytes()[18:20] == b"\x01\x01", f"{path.name} is still in WAL mode"


def test_a_backup_is_one_self_contained_file(tmp_path):
    database = _store(tmp_path)
    result = database.backup(tmp_path / "backups" / "one.sqlite3")
    _assert_whole(Path(result["path"]))
    assert _leftovers(tmp_path / "backups") == []


def test_backups_sharing_one_name_at_the_same_moment_each_finish_whole(tmp_path):
    database = _store(tmp_path)
    target = tmp_path / "backups" / "circuit-mcp-same-second.sqlite3"
    together = threading.Barrier(6)

    def backup(_):
        together.wait()
        return database.backup(target)

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(backup, range(6)))
    assert all(result["ok"] for result in results)
    _assert_whole(target)
    assert _leftovers(target.parent) == []


def test_a_backup_that_fails_leaves_nothing_behind(tmp_path, monkeypatch):
    database = _store(tmp_path)
    connect = database._connect

    class FailingSource:
        """The live store, failing the copy the way a full disk would."""

        def __init__(self):
            self.connection = connect()

        def backup(self, target):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            self.connection.close()

    monkeypatch.setattr(database, "_connect", FailingSource)
    try:
        database.backup(tmp_path / "backups" / "failed.sqlite3")
        raise AssertionError("the failed copy was reported as a backup")
    except sqlite3.OperationalError as exc:
        assert "disk I/O error" in str(exc)
    assert list((tmp_path / "backups").iterdir()) == []


def test_eight_backups_at_once_each_get_their_own_file_or_a_409():
    with TestClient(web.app, base_url=DESK) as browser:
        assert browser.get("/api/library").status_code == 200
        together = threading.Barrier(8)

        def backup(_):
            together.wait()
            return browser.post("/api/database/backup")

        with ThreadPoolExecutor(8) as pool:
            responses = list(pool.map(backup, range(8)))
        assert browser.get("/healthz").status_code == 200
    assert {response.status_code for response in responses} <= {200, 409}
    assert all(response.json()["detail"] == BUSY
               for response in responses if response.status_code == 409)
    paths = [Path(response.json()["path"]) for response in responses if response.status_code == 200]
    assert paths and len(set(paths)) == len(paths)
    assert sorted(path.name for path in (web.DATA / "backups").iterdir()) == sorted(
        path.name for path in paths)
    for path in paths:
        _assert_whole(path)


def test_a_backup_asked_for_while_one_runs_is_refused_with_409(monkeypatch):
    running, finish = threading.Event(), threading.Event()
    copy = CommandCenterDB.backup

    def slow(self, destination):
        if not running.is_set():
            running.set()
            assert finish.wait(10)
        return copy(self, destination)

    monkeypatch.setattr(CommandCenterDB, "backup", slow)
    with TestClient(web.app, base_url=DESK) as browser, ThreadPoolExecutor(1) as pool:
        first = pool.submit(browser.post, "/api/database/backup")
        assert running.wait(10)
        second = browser.post("/api/database/backup")
        finish.set()
        assert first.result().status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == BUSY
    assert [path.name for path in (web.DATA / "backups").iterdir()] == [
        Path(first.result().json()["path"]).name]
