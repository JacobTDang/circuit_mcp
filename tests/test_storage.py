from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

from circuit_mcp.storage import CommandCenterDB, StorageError


def database(tmp_path):
    data = tmp_path / "command_center"
    db = CommandCenterDB(data / "circuit_mcp.sqlite3")
    db.prepare(data / "library.json", data / "history.jsonl")
    return db, data


def add_document(db, data, name="notes.md", text="RC time constant", category="lecture"):
    identifier = hashlib.md5(f"{name}-{time.time_ns()}".encode()).hexdigest()
    payload = text.encode()
    files = data / "files"; files.mkdir(parents=True, exist_ok=True)
    path = files / f"{identifier}.md"; path.write_bytes(payload)
    return db.add_document({
        "id": identifier, "name": name, "category": category,
        "extension": ".md", "media_type": "text/markdown", "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(), "pages": None,
        "relative_path": f"files/{identifier}.md", "source": "upload", "created": time.time(),
    }, text)


def test_schema_pragmas_course_and_integrity(tmp_path):
    db, _ = database(tmp_path)
    with db._connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT code FROM courses").fetchone()[0] == "CIRCUITS"
        assert connection.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1
    assert db.integrity() == {"ok": True, "integrity_check": "ok", "foreign_key_violations": []}


def test_legacy_json_migration_is_verified_backed_up_and_idempotent(tmp_path):
    data = tmp_path / "command_center"; files = data / "files"; files.mkdir(parents=True)
    identifier = "a" * 32; payload = b"# RC\ntau=RC"
    (files / f"{identifier}.md").write_bytes(payload)
    index = data / "library.json"; history = data / "history.jsonl"
    index.write_text(json.dumps([{"id": identifier, "name": "week.md", "category": "lecture",
                                  "extension": ".md", "media_type": "text/markdown",
                                  "size": 1, "created": 10, "pages": None, "text": "tau=RC"}]))
    history.write_text(json.dumps({"time": 11, "kind": "upload", "name": "week.md", "ok": True}) + "\n")
    db = CommandCenterDB(data / "circuit_mcp.sqlite3")
    first = db.prepare(index, history)
    second = db.prepare(index, history)
    assert first["documents_migrated"] == 1 and first["events_migrated"] == 1
    assert second["documents_migrated"] == second["events_migrated"] == 0
    document = db.get_document(identifier)
    assert document["size"] == len(payload)
    assert document["sha256"] == hashlib.sha256(payload).hexdigest()
    assert db.events()[0]["name"] == "week.md"
    assert index.with_suffix(".json.migrated.bak").exists()
    assert history.with_suffix(".jsonl.migrated.bak").exists()


def test_invalid_legacy_index_fails_without_marking_migration_complete(tmp_path):
    data = tmp_path / "command_center"; data.mkdir()
    index = data / "library.json"; index.write_text("not json")
    db = CommandCenterDB(data / "circuit_mcp.sqlite3")
    with pytest.raises(StorageError, match="invalid"):
        db.prepare(index, data / "history.jsonl")
    index.write_text("[]")
    assert db.prepare(index, data / "history.jsonl")["documents_migrated"] == 0


def test_document_search_soft_delete_and_event_history(tmp_path):
    db, data = database(tmp_path)
    item = add_document(db, data)
    assert db.list_documents(query="TIME CONSTANT")[0]["id"] == item["id"]
    assert db.list_documents(category="lecture")[0]["sha256"]
    db.record_event("upload", item["name"], True, "document", item["id"], {"private": True})
    assert db.events()[0]["details"] == {"private": True}
    deleted = db.delete_document(item["id"])
    assert deleted["id"] == item["id"]
    assert db.list_documents() == []
    with pytest.raises(StorageError, match="not found"):
        db.get_document(item["id"])


def test_transcription_confirmation_preserves_revision_history(tmp_path):
    db, data = database(tmp_path); document = add_document(db, data)
    first = db.add_transcription(document["id"], "H(s)=1/(1-sRC)", "formula_ocr", "latex", "unimernet")
    corrected = db.confirm_transcription(first["id"], "H(s)=1/(1+sRC)")
    assert db.get_transcription(first["id"])["status"] == "rejected"
    assert corrected["status"] == "confirmed"
    assert corrected["supersedes_id"] == first["id"]
    assert corrected["confirmed_at"] is not None
    assert db.list_documents(query="1 sRC")[0]["id"] == document["id"]


def test_problem_attempt_evidence_tags_context_and_progress(tmp_path):
    db, data = database(tmp_path); document = add_document(db, data)
    problem = db.create_problem("RC pole", "filters", "Find the pole", document["id"])
    problem = db.update_problem(problem["id"], "series R, shunt C", "confirmed")
    assert db.tag_problem(problem["id"], "exam-1")["tags"] == ["exam-1"]
    attempt = db.create_attempt(problem["id"], "student", "-1/RC")
    evidence = db.record_tool_call("check_equivalence", {"expr_a": "-1/RC"}, {"ok": True}, 2.5, attempt["id"])
    assert evidence["ok"] is True
    done = db.complete_attempt(attempt["id"], "-1/RC", "correct")
    assert done["completed_at"] is not None
    history = db.attempt_history(problem["id"])
    assert history[0]["tool_calls"][0]["tool_name"] == "check_equivalence"
    assert db.course_progress()["attempts"] == {"correct": 1}
    context = db.study_context("RC")
    assert context["documents"][0]["id"] == document["id"]
    assert context["problems"][0]["id"] == problem["id"]


def test_repository_inputs_are_parameterized_and_bounded(tmp_path):
    db, data = database(tmp_path); document = add_document(db, data, name="safe.md")
    assert db.list_documents(query="%' OR 1=1 --") == []
    assert db.get_document(document["id"])["name"] == "safe.md"
    problem = db.create_problem("Safe", "topic", "prompt")
    with pytest.raises(StorageError, match="invalid tag"):
        db.tag_problem(problem["id"], "x'); DROP TABLE problems;--")
    assert db.get_problem(problem["id"])["title"] == "Safe"
    with pytest.raises(StorageError):
        db.create_problem("", "topic", "prompt")
    with pytest.raises(StorageError):
        db.complete_attempt("missing", "x", "working")


def test_concurrent_events_and_documents_do_not_lose_writes(tmp_path):
    db, data = database(tmp_path)
    errors = []
    def writer(index):
        try:
            add_document(db, data, f"note-{index}.md", f"body {index}")
            db.record_event("upload", f"note-{index}.md", True)
        except Exception as exc:  # test captures every worker failure
            errors.append(exc)
    threads = [threading.Thread(target=writer, args=(index,)) for index in range(40)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert errors == []
    assert len(db.list_documents(limit=100)) == 40
    assert len(db.events(limit=100)) == 40
    assert db.integrity()["ok"] is True


def test_file_audit_detects_missing_mismatch_and_reports_orphans(tmp_path):
    db, data = database(tmp_path)
    first = add_document(db, data, "first.md", "one")
    second = add_document(db, data, "second.md", "two")
    (data / "files" / "orphan.bin").write_bytes(b"orphan")
    assert db.audit_files()["orphans"] == ["orphan.bin"]
    (data / first["relative_path"]).write_bytes(b"changed")
    (data / second["relative_path"]).unlink()
    audit = db.audit_files()
    assert audit["ok"] is False
    assert audit["mismatched"] == [first["id"]]
    assert audit["missing"] == [second["id"]]


def test_online_backup_is_integral_and_restorable(tmp_path):
    db, data = database(tmp_path)
    document = add_document(db, data, "backup.md", "saved")
    target = tmp_path / "backups" / "snapshot.sqlite3"
    result = db.backup(target)
    assert result["ok"] is True and result["size_bytes"] > 0
    restored = CommandCenterDB(target)
    assert restored.integrity()["ok"] is True
    assert restored.get_document(document["id"])["name"] == "backup.md"
    with pytest.raises(StorageError):
        db.backup(db.path)
