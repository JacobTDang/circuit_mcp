"""Local SQLite persistence for the command center and study workflow.

Circuit solvers do not import this module.  Storage records context and
provenance; it never participates in a mathematical verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class StorageError(ValueError):
    """A bounded repository operation could not be completed."""


SCHEMA_VERSION = 1
COURSE_ID = "circuits"
COURSE_CODE = "CIRCUITS"
CATEGORIES = {"homework", "lecture", "reference", "solution"}
STATUSES = {"draft", "confirmed", "solved", "needs_review"}
ATTEMPT_STATUSES = {"working", "correct", "incorrect", "partial", "gap"}
UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def default_data_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("CIRCUIT_MCP_DATA_DIR", root / ".local" / "command_center")).expanduser().resolve()


def _number(value: Any) -> float | None:
    """Keep only finite numbers; a renderer that reports nonsense stores nothing."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _visual(row: Any) -> dict[str, Any]:
    """Serve an artifact through the local proxy; an upstream URL never leaves storage."""
    item = dict(row)
    item["spec"] = json.loads(item.pop("spec_json"))
    item["provenance"] = json.loads(item.pop("provenance_json"))
    item["url"] = f"/api/showman/objects/{item['object_key']}"
    return item


class CommandCenterDB:
    def __init__(self, path: Path | None = None):
        self.path = (path or default_data_dir() / "circuit_mcp.sqlite3").resolve()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def prepare(self, index_path: Path | None = None, history_path: Path | None = None) -> dict[str, Any]:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    checksum TEXT NOT NULL, applied_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS courses (
                    id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    institution TEXT NOT NULL, term TEXT, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, course_id TEXT REFERENCES courses(id),
                    name TEXT NOT NULL, category TEXT NOT NULL,
                    extension TEXT NOT NULL, media_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL, page_count INTEGER, source TEXT NOT NULL,
                    capture_json TEXT, created_at REAL NOT NULL, deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS documents_course_category ON documents(course_id, category, created_at);
                CREATE TABLE IF NOT EXISTS document_text (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    extracted_text TEXT NOT NULL, extractor TEXT NOT NULL,
                    extractor_version TEXT, created_at REAL NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(
                    document_id UNINDEXED, name, body, tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    kind TEXT NOT NULL, content TEXT NOT NULL, format TEXT NOT NULL,
                    model TEXT, device TEXT, duration_ms REAL,
                    status TEXT NOT NULL, created_at REAL NOT NULL,
                    confirmed_at REAL, supersedes_id TEXT REFERENCES transcriptions(id)
                );
                CREATE INDEX IF NOT EXISTS transcriptions_document ON transcriptions(document_id, created_at);
                CREATE TABLE IF NOT EXISTS problems (
                    id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id),
                    document_id TEXT REFERENCES documents(id), title TEXT NOT NULL,
                    topic TEXT NOT NULL, prompt TEXT NOT NULL,
                    circuit_interpretation TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, source_page INTEGER,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS problems_course_topic ON problems(course_id, topic, status);
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, problem_id TEXT NOT NULL REFERENCES problems(id),
                    actor TEXT NOT NULL, answer TEXT NOT NULL, status TEXT NOT NULL,
                    first_divergence TEXT, created_at REAL NOT NULL, completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS attempts_problem ON attempts(problem_id, created_at);
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts(id),
                    tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL,
                    result_json TEXT NOT NULL, ok INTEGER NOT NULL,
                    error_kind TEXT, duration_ms REAL, server_version TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tool_calls_attempt ON tool_calls(attempt_id, created_at);
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS problem_tags (
                    problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY(problem_id, tag_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                    entity_type TEXT, entity_id TEXT, display_name TEXT NOT NULL,
                    ok INTEGER NOT NULL, details_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_created ON events(created_at DESC);
                CREATE TABLE IF NOT EXISTS animation_scenes (
                    id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id),
                    problem_id TEXT REFERENCES problems(id) ON DELETE SET NULL,
                    title TEXT NOT NULL, scene_json TEXT NOT NULL,
                    revision INTEGER NOT NULL, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS animation_scenes_updated
                    ON animation_scenes(course_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS visual_assets (
                    id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id),
                    problem_id TEXT REFERENCES problems(id) ON DELETE SET NULL,
                    brief TEXT NOT NULL, object_key TEXT NOT NULL, spec_json TEXT NOT NULL,
                    duration_sec REAL, fps REAL, width INTEGER, height INTEGER,
                    provenance_json TEXT NOT NULL,
                    created_at REAL NOT NULL, deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS visual_assets_created
                    ON visual_assets(course_id, created_at DESC);
                """
            )
            checksum = hashlib.sha256(b"command-center-schema-v1").hexdigest()
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations VALUES(?, ?, ?, ?)",
                (SCHEMA_VERSION, "initial command-center schema", checksum, time.time()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO courses(id, code, title, institution, term, created_at) VALUES(?,?,?,?,?,?)",
                (COURSE_ID, COURSE_CODE, "Circuit Learning Workspace", "Local", None, time.time()),
            )
            legacy = connection.execute("SELECT id FROM courses WHERE id<>?", (COURSE_ID,)).fetchall()
            for row in legacy:
                for table in ("documents", "problems", "animation_scenes"):
                    connection.execute(f"UPDATE {table} SET course_id=? WHERE course_id=?", (COURSE_ID, row["id"]))
                connection.execute("DELETE FROM courses WHERE id=?", (row["id"],))
        migrated = self._migrate_legacy(index_path, history_path)
        return {"ok": True, "path": str(self.path), "schema_version": SCHEMA_VERSION, **migrated}

    def _migrate_legacy(self, index_path: Path | None, history_path: Path | None) -> dict[str, int]:
        with self._connect() as connection:
            done = connection.execute("SELECT value FROM settings WHERE key='legacy_migration_v1'").fetchone()
        if done:
            return {"documents_migrated": 0, "events_migrated": 0}
        documents_migrated = events_migrated = 0
        files_dir = self.path.parent / "files"
        with self.transaction() as connection:
            if index_path and index_path.exists():
                try:
                    items = json.loads(index_path.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    raise StorageError(f"legacy library index is invalid: {exc}") from exc
                if not isinstance(items, list):
                    raise StorageError("legacy library index must contain a list")
                for item in items:
                    identifier = str(item.get("id", ""))
                    extension = str(item.get("extension", ""))
                    file_path = files_dir / f"{identifier}{extension}"
                    if not UUID_RE.fullmatch(identifier) or not file_path.is_file():
                        raise StorageError(f"legacy document {identifier!r} has no valid local file")
                    payload_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
                    connection.execute(
                        """INSERT OR IGNORE INTO documents
                        (id,course_id,name,category,extension,media_type,relative_path,size_bytes,sha256,page_count,source,capture_json,created_at,deleted_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                        (identifier, COURSE_ID, str(item.get("name", file_path.name)),
                         str(item.get("category", "homework")), extension,
                         str(item.get("media_type", "application/octet-stream")),
                         f"files/{file_path.name}", file_path.stat().st_size, payload_hash,
                         item.get("pages"), "ipad_capture" if item.get("capture") else "upload",
                         json.dumps(item.get("capture")) if item.get("capture") else None,
                         float(item.get("created", file_path.stat().st_mtime))),
                    )
                    text = str(item.get("text", ""))
                    connection.execute(
                        "INSERT OR IGNORE INTO document_text VALUES(?,?,?,?,?)",
                        (identifier, text, "legacy", None, time.time()),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO document_search(document_id,name,body) VALUES(?,?,?)",
                        (identifier, str(item.get("name", file_path.name)), text),
                    )
                    documents_migrated += connection.execute("SELECT changes()").fetchone()[0]
                backup = index_path.with_suffix(index_path.suffix + ".migrated.bak")
                if not backup.exists():
                    shutil.copy2(index_path, backup)
            if history_path and history_path.exists():
                for line_number, line in enumerate(history_path.read_text().splitlines(), 1):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise StorageError(f"legacy history line {line_number} is invalid: {exc}") from exc
                    connection.execute(
                        "INSERT INTO events(event_type,entity_type,entity_id,display_name,ok,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
                        (str(event.get("kind", "legacy")), None, None, str(event.get("name", "")),
                         int(bool(event.get("ok"))), "{}", float(event.get("time", time.time()))),
                    )
                    events_migrated += 1
                backup = history_path.with_suffix(history_path.suffix + ".migrated.bak")
                if not backup.exists():
                    shutil.copy2(history_path, backup)
            connection.execute("INSERT OR REPLACE INTO settings VALUES('legacy_migration_v1', ?)", (str(time.time()),))
        return {"documents_migrated": documents_migrated, "events_migrated": events_migrated}

    def add_document(self, item: dict[str, Any], text: str = "") -> dict[str, Any]:
        if item.get("category") not in CATEGORIES:
            raise StorageError("invalid document category")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO documents
                (id,course_id,name,category,extension,media_type,relative_path,size_bytes,sha256,page_count,source,capture_json,created_at,deleted_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (item["id"], item.get("course_id", COURSE_ID), item["name"], item["category"],
                 item["extension"], item["media_type"], item["relative_path"], item["size"],
                 item["sha256"], item.get("pages"), item.get("source", "upload"),
                 json.dumps(item.get("capture")) if item.get("capture") else None, item["created"]),
            )
            connection.execute(
                "INSERT INTO document_text VALUES(?,?,?,?,?)",
                (item["id"], text, item.get("extractor", "builtin"), item.get("extractor_version"), time.time()),
            )
            connection.execute("INSERT INTO document_search(document_id,name,body) VALUES(?,?,?)", (item["id"], item["name"], text))
        return self.get_document(item["id"], include_text=False)

    @staticmethod
    def _refresh_search(connection: sqlite3.Connection, document_id: str) -> None:
        row = connection.execute(
            """SELECT d.name, COALESCE(dt.extracted_text,'') extracted_text
               FROM documents d LEFT JOIN document_text dt ON dt.document_id=d.id
               WHERE d.id=? AND d.deleted_at IS NULL""", (document_id,)
        ).fetchone()
        connection.execute("DELETE FROM document_search WHERE document_id=?", (document_id,))
        if row is None: return
        confirmed = "\n".join(value[0] for value in connection.execute(
            "SELECT content FROM transcriptions WHERE document_id=? AND status='confirmed' ORDER BY created_at",
            (document_id,),
        ))
        connection.execute(
            "INSERT INTO document_search(document_id,name,body) VALUES(?,?,?)",
            (document_id, row["name"], row["extracted_text"] + "\n" + confirmed),
        )

    @staticmethod
    def _document(row: sqlite3.Row, include_text: bool = False) -> dict[str, Any]:
        item = {
            "id": row["id"], "name": row["name"], "category": row["category"],
            "extension": row["extension"], "media_type": row["media_type"],
            "size": row["size_bytes"], "sha256": row["sha256"],
            "created": row["created_at"], "pages": row["page_count"],
            "source": row["source"], "relative_path": row["relative_path"],
        }
        if row["capture_json"]:
            item["capture"] = json.loads(row["capture_json"])
        if include_text:
            item["text"] = row["extracted_text"] or ""
        return item

    def list_documents(self, query: str = "", category: str = "", limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses, values = ["d.deleted_at IS NULL"], []
        if category:
            if category not in CATEGORIES:
                raise StorageError("invalid document category")
            clauses.append("d.category=?"); values.append(category)
        if query.strip():
            if len(query) > 500: raise StorageError("search query is too long")
            tokens = re.findall(r"\w+", query.casefold(), re.UNICODE)
            if not tokens: return []
            match = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens[:20])
            clauses.append("d.id IN (SELECT document_id FROM document_search WHERE document_search MATCH ?)")
            values.append(match)
        sql = f"""SELECT d.*, t.extracted_text FROM documents d
                  LEFT JOIN document_text t ON t.document_id=d.id
                  WHERE {' AND '.join(clauses)} ORDER BY d.created_at DESC LIMIT ?"""
        values.append(limit)
        with self._connect() as connection:
            return [self._document(row) for row in connection.execute(sql, values)]

    def get_document(self, identifier: str, include_text: bool = True) -> dict[str, Any]:
        if not UUID_RE.fullmatch(identifier):
            raise StorageError("invalid document id")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT d.*, t.extracted_text FROM documents d LEFT JOIN document_text t ON t.document_id=d.id
                   WHERE d.id=? AND d.deleted_at IS NULL""", (identifier,)
            ).fetchone()
        if row is None:
            raise StorageError("document not found")
        return self._document(row, include_text)

    def delete_document(self, identifier: str) -> dict[str, Any]:
        document = self.get_document(identifier, include_text=False)
        with self.transaction() as connection:
            connection.execute("UPDATE documents SET deleted_at=? WHERE id=? AND deleted_at IS NULL", (time.time(), identifier))
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise StorageError("document not found")
            connection.execute("DELETE FROM document_search WHERE document_id=?", (identifier,))
        return document

    def record_event(self, event_type: str, display_name: str, ok: bool, entity_type: str | None = None,
                     entity_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO events(event_type,entity_type,entity_id,display_name,ok,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (event_type, entity_type, entity_id, display_name, int(ok), json.dumps(details or {}, separators=(",", ":")), time.time()),
            )

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": row["id"], "time": row["created_at"], "kind": row["event_type"],
                 "name": row["display_name"], "ok": bool(row["ok"]),
                 "entity_type": row["entity_type"], "entity_id": row["entity_id"],
                 "details": json.loads(row["details_json"])} for row in rows]

    def add_transcription(self, document_id: str, content: str, kind: str, format: str,
                          model: str | None = None, device: str | None = None,
                          duration_ms: float | None = None, status: str = "unconfirmed",
                          supersedes_id: str | None = None) -> dict[str, Any]:
        if status not in {"unconfirmed", "confirmed", "rejected"} or not content.strip() or len(content) > 2_000_000:
            raise StorageError("invalid transcription")
        self.get_document(document_id, include_text=False)
        identifier, now = uuid.uuid4().hex, time.time()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO transcriptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, document_id, kind, content, format, model, device, duration_ms,
                 status, now, now if status == "confirmed" else None, supersedes_id),
            )
            if status == "confirmed": self._refresh_search(connection, document_id)
        return self.get_transcription(identifier)

    def get_transcription(self, identifier: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM transcriptions WHERE id=?", (identifier,)).fetchone()
        if row is None: raise StorageError("transcription not found")
        return dict(row)

    def confirm_transcription(self, identifier: str, corrected_content: str | None = None) -> dict[str, Any]:
        current = self.get_transcription(identifier)
        if corrected_content is not None and corrected_content.strip() != current["content"].strip():
            with self.transaction() as connection:
                connection.execute("UPDATE transcriptions SET status='rejected' WHERE id=?", (identifier,))
            return self.add_transcription(current["document_id"], corrected_content, current["kind"], current["format"],
                                          current["model"], current["device"], current["duration_ms"], "confirmed", identifier)
        with self.transaction() as connection:
            connection.execute("UPDATE transcriptions SET status='confirmed', confirmed_at=? WHERE id=?", (time.time(), identifier))
            self._refresh_search(connection, current["document_id"])
        return self.get_transcription(identifier)

    def create_problem(self, title: str, topic: str, prompt: str, document_id: str | None = None,
                       circuit_interpretation: str = "", status: str = "draft", source_page: int | None = None) -> dict[str, Any]:
        if status not in STATUSES or not title.strip() or not topic.strip() or not prompt.strip():
            raise StorageError("title, topic, prompt, and a valid status are required")
        if max(map(len, (title, topic, prompt, circuit_interpretation))) > 200_000:
            raise StorageError("problem text is too large")
        if document_id: self.get_document(document_id, include_text=False)
        identifier, now = uuid.uuid4().hex, time.time()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO problems VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, COURSE_ID, document_id, title, topic, prompt, circuit_interpretation,
                 status, source_page, now, now),
            )
        return self.get_problem(identifier)

    def create_animation(self, scene: dict[str, Any], problem_id: str | None = None) -> dict[str, Any]:
        if problem_id: self.get_problem(problem_id)
        identifier, now = uuid.uuid4().hex, time.time()
        payload = json.dumps(scene, separators=(",", ":"), ensure_ascii=False)
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO animation_scenes VALUES(?,?,?,?,?,?,?,?,NULL)",
                (identifier, COURSE_ID, problem_id, scene["title"], payload, 1, now, now),
            )
        return self.get_animation(identifier)

    def get_animation(self, identifier: str) -> dict[str, Any]:
        if not UUID_RE.fullmatch(identifier): raise StorageError("animation not found")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM animation_scenes WHERE id=? AND deleted_at IS NULL", (identifier,)
            ).fetchone()
        if row is None: raise StorageError("animation not found")
        result = dict(row); result["scene"] = json.loads(result.pop("scene_json")); return result

    def list_animations(self, updated_after: float = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM animation_scenes WHERE course_id=? AND deleted_at IS NULL AND updated_at>? ORDER BY updated_at DESC LIMIT ?",
                (COURSE_ID, float(updated_after), max(1, min(int(limit), 200))),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row); item["scene"] = json.loads(item.pop("scene_json")); results.append(item)
        return results

    def update_animation(self, identifier: str, scene: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE animation_scenes SET title=?,scene_json=?,revision=revision+1,updated_at=? WHERE id=? AND deleted_at IS NULL",
                (scene["title"], json.dumps(scene, separators=(",", ":"), ensure_ascii=False), time.time(), identifier),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1: raise StorageError("animation not found")
        return self.get_animation(identifier)

    def delete_animation(self, identifier: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE animation_scenes SET deleted_at=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                               (time.time(), time.time(), identifier))
            if connection.execute("SELECT changes()").fetchone()[0] != 1: raise StorageError("animation not found")

    # -- Showman-rendered visuals ------------------------------------------

    def create_visual(self, brief: str, render: dict[str, Any],
                      problem_id: str | None = None) -> dict[str, Any]:
        """Record one rendered visual. A render with no artifact never becomes a row."""
        video = render.get("video")
        key = video.get("key") if isinstance(video, dict) else None
        if not isinstance(key, str) or not key:
            raise StorageError("render carries no object key")
        if problem_id:
            self.get_problem(problem_id)
        identifier, now = uuid.uuid4().hex, time.time()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO visual_assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (identifier, COURSE_ID, problem_id, brief, key,
                 json.dumps(render.get("spec") or {}, separators=(",", ":"), ensure_ascii=False),
                 _number(render.get("durationSec")), _number(render.get("fps")),
                 _number(render.get("width")), _number(render.get("height")),
                 json.dumps(render.get("provenance") or {}, separators=(",", ":"), ensure_ascii=False),
                 now),
            )
        return self.get_visual(identifier)

    def get_visual(self, identifier: str) -> dict[str, Any]:
        if not UUID_RE.fullmatch(identifier):
            raise StorageError("visual not found")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM visual_assets WHERE id=? AND deleted_at IS NULL", (identifier,)
            ).fetchone()
        if row is None:
            raise StorageError("visual not found")
        return _visual(row)

    def list_visuals(self, problem_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clause = " AND problem_id=?" if problem_id else ""
        parameters: tuple[Any, ...] = (COURSE_ID, problem_id) if problem_id else (COURSE_ID,)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM visual_assets WHERE course_id=?" + clause
                + " AND deleted_at IS NULL ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*parameters, max(1, min(int(limit), 200))),
            ).fetchall()
        return [_visual(row) for row in rows]

    def expire_unlinked_visuals(self, cutoff: float) -> list[str]:
        """Soft-delete ad-hoc visuals older than the cutoff and report their keys.

        A visual attached to a problem is exempt: it is the work that cost
        something, and the clock has no claim on it.
        """
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT id,object_key FROM visual_assets "
                "WHERE deleted_at IS NULL AND problem_id IS NULL AND created_at<?",
                (float(cutoff),),
            ).fetchall()
            if rows:
                connection.executemany(
                    "UPDATE visual_assets SET deleted_at=? WHERE id=?",
                    [(time.time(), row["id"]) for row in rows],
                )
        return [row["object_key"] for row in rows]

    def age_visual_for_test(self, identifier: str, created_at: float) -> None:
        """Backdate one visual. Tests need an old row; nothing else may use this."""
        with self.transaction() as connection:
            connection.execute("UPDATE visual_assets SET created_at=? WHERE id=?",
                               (float(created_at), identifier))

    def delete_visual(self, identifier: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE visual_assets SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                               (time.time(), identifier))
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise StorageError("visual not found")

    def get_problem(self, identifier: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM problems WHERE id=?", (identifier,)).fetchone()
            if row is None: raise StorageError("problem not found")
            tags = [tag[0] for tag in connection.execute(
                "SELECT t.name FROM tags t JOIN problem_tags pt ON pt.tag_id=t.id WHERE pt.problem_id=? ORDER BY t.name", (identifier,))]
        result = dict(row); result["tags"] = tags; return result

    def list_problems(self, topic: str = "", status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        clauses, values = ["course_id=?"], [COURSE_ID]
        if topic: clauses.append("topic=?"); values.append(topic)
        if status:
            if status not in STATUSES: raise StorageError("invalid problem status")
            clauses.append("status=?"); values.append(status)
        values.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM problems WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?", values).fetchall()
        return [dict(row) for row in rows]

    def update_problem(self, identifier: str, circuit_interpretation: str, status: str) -> dict[str, Any]:
        if status not in STATUSES: raise StorageError("invalid problem status")
        with self.transaction() as connection:
            connection.execute("UPDATE problems SET circuit_interpretation=?,status=?,updated_at=? WHERE id=?",
                               (circuit_interpretation, status, time.time(), identifier))
            if connection.execute("SELECT changes()").fetchone()[0] != 1: raise StorageError("problem not found")
        return self.get_problem(identifier)

    def tag_problem(self, identifier: str, tag: str) -> dict[str, Any]:
        self.get_problem(identifier)
        normalized = tag.casefold().strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,49}", normalized): raise StorageError("invalid tag")
        with self.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (normalized,))
            connection.execute("INSERT OR IGNORE INTO problem_tags(problem_id,tag_id) SELECT ?,id FROM tags WHERE name=?", (identifier, normalized))
        return self.get_problem(identifier)

    def create_attempt(self, problem_id: str, actor: str, answer: str = "", status: str = "working") -> dict[str, Any]:
        self.get_problem(problem_id)
        if status not in ATTEMPT_STATUSES or not actor.strip() or len(answer) > 2_000_000: raise StorageError("invalid attempt")
        identifier, now = uuid.uuid4().hex, time.time()
        with self.transaction() as connection:
            connection.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
                               (identifier, problem_id, actor, answer, status, None, now, None))
        return self.get_attempt(identifier)

    def get_attempt(self, identifier: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM attempts WHERE id=?", (identifier,)).fetchone()
        if row is None: raise StorageError("attempt not found")
        return dict(row)

    def complete_attempt(self, identifier: str, answer: str, status: str, first_divergence: str | None = None) -> dict[str, Any]:
        if status not in ATTEMPT_STATUSES - {"working"}: raise StorageError("completed attempt needs a final status")
        with self.transaction() as connection:
            connection.execute("UPDATE attempts SET answer=?,status=?,first_divergence=?,completed_at=? WHERE id=?",
                               (answer, status, first_divergence, time.time(), identifier))
            if connection.execute("SELECT changes()").fetchone()[0] != 1: raise StorageError("attempt not found")
        return self.get_attempt(identifier)

    def record_tool_call(self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any],
                         duration_ms: float, attempt_id: str | None = None) -> dict[str, Any]:
        if attempt_id: self.get_attempt(attempt_id)
        arguments_json = json.dumps(arguments, separators=(",", ":"), allow_nan=False)
        result_json = json.dumps(result, separators=(",", ":"), allow_nan=False)
        if len(arguments_json) > 5_000_000 or len(result_json) > 10_000_000: raise StorageError("tool evidence is too large")
        identifier = uuid.uuid4().hex
        with self.transaction() as connection:
            connection.execute("INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (identifier, attempt_id, tool_name, arguments_json, result_json,
                                int(bool(result.get("ok"))), result.get("error"), duration_ms, "0.1.0", time.time()))
        return {"id": identifier, "attempt_id": attempt_id, "tool_name": tool_name, "ok": bool(result.get("ok"))}

    def attempt_history(self, problem_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self.get_problem(problem_id)
        with self._connect() as connection:
            attempts = [dict(row) for row in connection.execute(
                "SELECT * FROM attempts WHERE problem_id=? ORDER BY created_at DESC LIMIT ?", (problem_id, max(1, min(limit, 500))))]
            for attempt in attempts:
                attempt["tool_calls"] = [dict(row) for row in connection.execute(
                    "SELECT id,tool_name,ok,error_kind,duration_ms,created_at FROM tool_calls WHERE attempt_id=? ORDER BY created_at", (attempt["id"],))]
        return attempts

    def course_progress(self) -> dict[str, Any]:
        with self._connect() as connection:
            problem_rows = connection.execute("SELECT status,count(*) count FROM problems WHERE course_id=? GROUP BY status", (COURSE_ID,)).fetchall()
            attempt_rows = connection.execute("SELECT status,count(*) count FROM attempts GROUP BY status").fetchall()
            topics = connection.execute("SELECT topic,count(*) count FROM problems WHERE course_id=? GROUP BY topic ORDER BY topic", (COURSE_ID,)).fetchall()
        return {"ok": True, "course": COURSE_CODE,
                "problems": {row["status"]: row["count"] for row in problem_rows},
                "attempts": {row["status"]: row["count"] for row in attempt_rows},
                "topics": {row["topic"]: row["count"] for row in topics}}

    def study_context(self, query: str, limit: int = 10) -> dict[str, Any]:
        documents = self.list_documents(query=query, limit=limit)
        needle = f"%{query.casefold().strip()}%"
        with self._connect() as connection:
            problems = [dict(row) for row in connection.execute(
                "SELECT * FROM problems WHERE lower(title) LIKE ? OR lower(topic) LIKE ? OR lower(prompt) LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (needle, needle, needle, max(1, min(limit, 50))))]
        return {"ok": True, "query": query, "documents": documents, "problems": problems}

    def integrity(self) -> dict[str, Any]:
        with self._connect() as connection:
            check = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        return {"ok": check == "ok" and not foreign, "integrity_check": check,
                "foreign_key_violations": [tuple(row) for row in foreign]}

    def audit_files(self, files_dir: Path | None = None) -> dict[str, Any]:
        root = (files_dir or self.path.parent / "files").resolve()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,relative_path,sha256,size_bytes FROM documents WHERE deleted_at IS NULL"
            ).fetchall()
        missing, mismatched, referenced = [], [], set()
        for row in rows:
            path = (self.path.parent / row["relative_path"]).resolve()
            referenced.add(path)
            if not path.is_file(): missing.append(row["id"]); continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != row["sha256"] or path.stat().st_size != row["size_bytes"]:
                mismatched.append(row["id"])
        orphans = [] if not root.exists() else [
            str(path.relative_to(root)) for path in root.iterdir()
            if path.is_file() and path.resolve() not in referenced
        ]
        return {"ok": not missing and not mismatched, "checked": len(rows),
                "missing": missing, "mismatched": mismatched, "orphans": sorted(orphans)}

    def backup(self, destination: Path) -> dict[str, Any]:
        target = destination.expanduser().resolve()
        if target == self.path or target.is_dir(): raise StorageError("backup destination must be a different file")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".temporary")
        temporary.unlink(missing_ok=True)
        source_connection = self._connect()
        backup_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(backup_connection)
            backup_connection.execute("PRAGMA wal_checkpoint(FULL)")
            check = backup_connection.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok": raise StorageError(f"backup integrity check failed: {check}")
        finally:
            backup_connection.close(); source_connection.close()
        os.replace(temporary, target)
        return {"ok": True, "path": str(target), "size_bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
