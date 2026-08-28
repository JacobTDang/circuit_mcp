# Local study database

The command center stores workflow state in
`.local/command_center/circuit_mcp.sqlite3`. Uploaded PDFs, images, notes, and
CSV files remain under `.local/command_center/files/`; SQLite stores their
relative paths, sizes, and SHA-256 hashes rather than binary blobs.

Circuit solvers remain stateless. `derive`, SPICE, equivalence, metrics, and
other mathematical tools do not consult stored answers. The web orchestration
layer records tool arguments/results as evidence, and the scoped MCP context
tools expose only repository operations—never SQL or arbitrary filesystem
paths.

## Data model

- `courses`: the local EE 2300 course record.
- `documents` and `document_text`: file metadata, provenance, hashes, and
  bounded extracted text.
- `document_search`: FTS5 index over names, extracted text, and confirmed
  transcription revisions.
- `transcriptions`: immutable OCR/vision revisions with explicit confirmation
  state and `supersedes_id` links.
- `problems`: prompts, topics, source documents, confirmed circuit
  interpretations, and workflow status.
- `attempts`: student/agent answers and outcomes.
- `tool_calls`: bounded JSON arguments/results linked to attempts when known.
- `tags` and `problem_tags`: normalized course labels.
- `events`: durable command-center activity history.
- `schema_migrations` and `settings`: migration identity and idempotence.

Every connection enables foreign keys, WAL, normal synchronous mode, and a
five-second busy timeout. Repository writes use explicit immediate
transactions and parameterized SQL.

## Legacy migration

The first database preparation imports `library.json` and `history.jsonl` in a
single transaction. Every document ID and local file is validated, and file
size/hash values are recomputed rather than trusted from JSON. Successful
migration creates `*.migrated.bak` copies and records `legacy_migration_v1`, so
later starts cannot duplicate rows. `workspace.json` is deliberately not
migrated because it is capture configuration, not study history.

## Scoped MCP context tools

Read-only:

- `library_search`
- `document_get`
- `problem_get`
- `study_context`
- `attempt_history`
- `course_progress`

Bounded writes:

- `problem_create`
- `problem_update_interpretation`
- `transcription_confirm`
- `attempt_create`
- `attempt_complete`
- `problem_tag`

Document upload/deletion, backups, and raw file access remain UI operations.
There is no arbitrary SQL MCP tool.

## Backup and recovery

Create a consistent online backup:

```console
PYTHONPATH=src .venv/bin/python scripts/backup_database.py
```

Or select a destination:

```console
PYTHONPATH=src .venv/bin/python scripts/backup_database.py /safe/path/circuit-mcp.sqlite3
```

The script checks database integrity, foreign keys, file hashes, missing files,
and orphans before publishing an atomic SQLite backup. The command center also
offers `POST /api/database/backup` with a server-selected local destination.

Document deletion is soft in SQLite and moves the file to
`.local/command_center/trash/`, making recovery possible. Restoring an entire
database means stopping the UI, preserving the current database and WAL/SHM
files, copying a verified backup into place, then restarting and checking
`GET /api/database/integrity`.

## Verification

Storage tests cover migration reruns, invalid legacy rollback, FTS, immutable
transcription corrections, problem/attempt/evidence relations, SQL-injection
strings, bounds, concurrent writers, file tampering, missing/orphan files,
online backup/restore, web APIs, and the real stdio MCP transport. The live
migration retained 24/24 files with matching hashes and imported 383 events.
