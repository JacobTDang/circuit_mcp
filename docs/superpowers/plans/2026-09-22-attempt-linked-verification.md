# Attempt-linked verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record what the MCP verification tools proved against the attempt they verify, so the board and a later session can see it.

**Architecture:** An optional `attempt_id` on `derive`, `check_derivation`, `check_setup`, `check_equivalence` and `simulate_spice`, routed through one `_recorded` helper that validates the attempt, runs the existing `_guarded` worker call, and writes the evidence from the parent process. Because `ok` means a different thing in each tool, the verdict is computed once at write time by `storage.verdict_for` and stored in a new `tool_calls.verdict` column (schema v3, with a backfill).

**Tech Stack:** Python 3.12, SQLite via `sqlite3`, FastMCP, FastAPI, pytest, plain-JS front end.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-22-attempt-linked-verification-design.md`.
- Calls without `attempt_id` must behave exactly as today, result keys included.
- The worker subprocess never touches the database; recording happens in the parent, as `canvas_card_add` already does.
- An unknown `attempt_id` fails **before** the tool runs.
- A storage failure after a successful run returns the result plus `evidence_warning`, never discards it.
- `check_equivalence` settled by the numeric oracle records `pass`; `result_json` keeps `oracle`.
- Run tests with `uv run python -m pytest` until #47 (PR #58) merges; after that `uv run pytest` works too.
- Migrations are forward-only: a step raises rather than half-applying.

---

### Task 1: The verdict rule and schema v3

**Files:**
- Modify: `src/circuit_mcp/storage.py:26` (`SCHEMA_VERSION`), `:122` (`MIGRATIONS`), `:229` (`tool_calls` baseline)
- Test: `tests/test_storage.py`, `tests/test_migrations.py:74`

**Interfaces:**
- Consumes: nothing.
- Produces: `storage.verdict_for(tool_name: str, result: dict[str, Any]) -> str` returning one of `"pass" | "fail" | "computed" | "error"`; `tool_calls.verdict` column; migration `(3, "add tool_call verdict", _add_tool_call_verdict)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_storage.py`, append:

```python
def test_verdict_names_what_each_tool_proved():
    assert storage.verdict_for("check_derivation", {"ok": True, "kind": "ok"}) == "pass"
    assert storage.verdict_for("check_derivation", {"ok": False, "kind": "algebra"}) == "fail"
    assert storage.verdict_for("check_setup", {"ok": False, "kind": "not_satisfied"}) == "fail"
    assert storage.verdict_for("check_equivalence", {"ok": True, "equivalent": True, "oracle": "symbolic"}) == "pass"
    assert storage.verdict_for("check_equivalence", {"ok": True, "equivalent": False, "oracle": "numeric"}) == "fail"
    assert storage.verdict_for("derive", {"ok": True, "transfer_function": {}}) == "computed"
    assert storage.verdict_for("simulate_spice", {"ok": True, "points": []}) == "computed"
    assert storage.verdict_for("derive", {"ok": False, "error": "circuit_error", "message": "bad netlist"}) == "error"


def test_numeric_oracle_equivalence_counts_as_pass():
    result = {"ok": True, "equivalent": True, "oracle": "numeric", "counterexample": None}
    assert storage.verdict_for("check_equivalence", result) == "pass"
```

In `tests/test_migrations.py`, append:

```python
def test_migration_3_backfills_verdicts(tmp_path):
    db = CommandCenterDB(tmp_path / "command_center" / "circuit_mcp.sqlite3")
    db.prepare()
    with db.transaction() as connection:
        connection.execute("UPDATE tool_calls SET verdict=NULL")
        for identifier, tool, result in (
            ("a", "check_derivation", '{"ok":true,"kind":"ok"}'),
            ("b", "check_derivation", '{"ok":false,"kind":"algebra"}'),
            ("c", "check_equivalence", '{"ok":true,"equivalent":false,"oracle":"numeric"}'),
            ("d", "derive", '{"ok":true}'),
        ):
            connection.execute(
                "INSERT INTO tool_calls (id,attempt_id,tool_name,arguments_json,result_json,ok,error_kind,"
                "duration_ms,server_version,created_at,verdict) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                (identifier, None, tool, "{}", result, 1, None, 1.0, "0.1.0", 1.0))
        connection.execute("DELETE FROM schema_migrations WHERE version=3")
    db.prepare()
    with db._connect() as connection:
        verdicts = dict(connection.execute("SELECT id,verdict FROM tool_calls"))
    assert verdicts == {"a": "pass", "b": "fail", "c": "fail", "d": "computed"}
```

And change the two existing assertions that read `applied_versions(db) == [1, 2]` to `== [1, 2, 3]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_storage.py::test_verdict_names_what_each_tool_proved tests/test_migrations.py -q`
Expected: FAIL — `AttributeError: module 'circuit_mcp.storage' has no attribute 'verdict_for'`, and the version lists still read `[1, 2]`.

- [ ] **Step 3: Implement the rule, the column and the migration**

In `src/circuit_mcp/storage.py`, set `SCHEMA_VERSION = 3`. Add `verdict TEXT` as the **last** column of the `tool_calls` baseline so a fresh store and a migrated store agree:

```python
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts(id),
                    tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL,
                    result_json TEXT NOT NULL, ok INTEGER NOT NULL,
                    error_kind TEXT, duration_ms REAL, server_version TEXT,
                    created_at REAL NOT NULL, verdict TEXT
                );
```

Above `MIGRATIONS`, add the rule and the step:

```python
_JUDGING_TOOLS = frozenset({"check_derivation", "check_setup"})


def verdict_for(tool_name: str, result: dict[str, Any]) -> str:
    """What a recorded call proved.

    ``ok`` cannot answer this on its own: it means "the work passed" for
    ``check_derivation`` and ``check_setup``, "the comparison ran" for
    ``check_equivalence``, and "it computed" for ``derive`` and
    ``simulate_spice``. Deciding once, at write time, is what keeps every
    reader from parsing up to 10 MB of stored result.
    """
    if result.get("error"):
        return "error"
    if tool_name in _JUDGING_TOOLS:
        return "pass" if result.get("ok") else "fail"
    if not result.get("ok"):
        return "error"
    if tool_name == "check_equivalence":
        return "pass" if result.get("equivalent") else "fail"
    return "computed"


def _add_tool_call_verdict(db: CommandCenterDB, connection: sqlite3.Connection) -> None:
    """Record what each stored call proved, for rows written before the column."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(tool_calls)")}
    if "verdict" not in columns:
        connection.execute("ALTER TABLE tool_calls ADD COLUMN verdict TEXT")
    rows = connection.execute(
        "SELECT id,tool_name,result_json FROM tool_calls WHERE verdict IS NULL").fetchall()
    for identifier, tool_name, result_json in rows:
        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:
            # A row we cannot read is still a row that ran; it is not a verdict.
            result = {}
        connection.execute("UPDATE tool_calls SET verdict=? WHERE id=?",
                           (verdict_for(tool_name, result), identifier))
```

Extend the registry:

```python
MIGRATIONS: tuple[tuple[int, str, Callable[[CommandCenterDB, sqlite3.Connection], None]], ...] = (
    (2, "drop legacy animation_scenes", _drop_animation_scenes),
    (3, "add tool_call verdict", _add_tool_call_verdict),
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_storage.py tests/test_migrations.py -q`
Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/storage.py tests/test_storage.py tests/test_migrations.py
git commit -m "Store what each recorded tool call proved, in a verdict column"
```

---

### Task 2: Write the verdict, and read it back with the arguments

**Files:**
- Modify: `src/circuit_mcp/storage.py:731` (`record_tool_call`), `:744` (`attempt_history`)
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `verdict_for` from Task 1.
- Produces: `record_tool_call(...)` returns `{"id", "attempt_id", "tool_name", "ok", "verdict"}`; each entry of `attempt_history(...)[i]["tool_calls"]` carries `verdict` and `arguments` (a dict), alongside the existing `id`, `tool_name`, `ok`, `error_kind`, `duration_ms`, `created_at`.

- [ ] **Step 1: Write the failing test**

In `tests/test_storage.py`:

```python
def test_attempt_history_shows_each_check_verdict_and_its_arguments(tmp_path):
    db, data = database(tmp_path)
    document = add_document(db, data)
    problem = db.create_problem("Inverting gain", "op-amps", "Find vo/vi", document["id"])
    attempt = db.create_attempt(problem["id"], "student")
    db.record_tool_call("check_derivation", {"steps": ["R2/R1"], "truth": "R2/R1"},
                        {"ok": True, "kind": "ok"}, 12.0, attempt["id"])
    db.record_tool_call("check_equivalence", {"expr_a": "a", "expr_b": "b"},
                        {"ok": True, "equivalent": False, "oracle": "numeric"}, 8.0, attempt["id"])
    db.record_tool_call("derive", {"netlist": "R1 1 0 {R}"},
                        {"ok": True, "transfer_function": {"text": "1"}}, 30.0, attempt["id"])

    calls = db.attempt_history(problem["id"])[0]["tool_calls"]

    assert [call["verdict"] for call in calls] == ["pass", "fail", "computed"]
    assert calls[0]["arguments"] == {"steps": ["R2/R1"], "truth": "R2/R1"}
    assert "arguments_json" not in calls[0]
```

Check the exact signatures of `create_problem` and `create_attempt` in `storage.py` before running, and match them.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_storage.py::test_attempt_history_shows_each_check_verdict_and_its_arguments -q`
Expected: FAIL with `KeyError: 'verdict'`.

- [ ] **Step 3: Implement**

In `record_tool_call`, name the columns in the insert rather than relying on their order, and store the verdict:

```python
        identifier = uuid.uuid4().hex
        verdict = verdict_for(tool_name, result)
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO tool_calls (id,attempt_id,tool_name,arguments_json,result_json,ok,"
                "error_kind,duration_ms,server_version,created_at,verdict) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, attempt_id, tool_name, arguments_json, result_json,
                 int(bool(result.get("ok"))), result.get("error"), duration_ms, "0.1.0", time.time(), verdict))
        return {"id": identifier, "attempt_id": attempt_id, "tool_name": tool_name,
                "ok": bool(result.get("ok")), "verdict": verdict}
```

In `attempt_history`, select the two new fields and decode the arguments:

```python
            for attempt in attempts:
                calls = []
                for row in connection.execute(
                        "SELECT id,tool_name,ok,verdict,error_kind,duration_ms,created_at,arguments_json "
                        "FROM tool_calls WHERE attempt_id=? ORDER BY created_at", (attempt["id"],)):
                    call = dict(row)
                    call["arguments"] = json.loads(call.pop("arguments_json"))
                    calls.append(call)
                attempt["tool_calls"] = calls
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_storage.py tests/test_web.py -q`
Expected: PASS. `test_web.py` covers the existing `/api/tools/{name}` recording path and must stay green.

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/storage.py tests/test_storage.py
git commit -m "Return each recorded call's verdict and arguments from attempt_history"
```

---

### Task 3: `attempt_id` on the five verification tools

**Files:**
- Modify: `src/circuit_mcp/server.py` (`_recorded` beside `_guarded` at `:973`; the tools at `:1069` `derive`, `:1111` `check_equivalence`, `:1126` `check_derivation`, `:1173` `check_setup`, `:1209` `simulate_spice`)
- Modify: `CLAUDE.md` (workflow steps 6–7), `README.md`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `record_tool_call` from Task 2; the existing `_guarded(name, **kwargs)` and `_storage()`.
- Produces: `_recorded(name: str, attempt_id: str | None, **kwargs: Any) -> dict[str, Any]`. With an `attempt_id` the result gains `evidence_id: str` and `verdict: str`; on a storage failure after a good run it gains `evidence_warning: str` instead.

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py`:

```python
def _attempt(tmp_path, monkeypatch):
    """A problem with one open attempt, in a throwaway store."""
    data = tmp_path / "command_center"
    monkeypatch.setattr(server_module, "default_data_dir", lambda: data)
    database = server_module._storage()
    problem = database.create_problem("Inverting gain", "op-amps", "Find vo/vi", None)
    return database, problem, database.create_attempt(problem["id"], "student")


def test_derive_with_attempt_id_records_arguments_and_result(tmp_path, monkeypatch):
    database, problem, attempt = _attempt(tmp_path, monkeypatch)

    result = server_module.derive("R1 1 0 {R}\nVs 1 0 {V}", 1, 0, 1, 0, "finite", attempt_id=attempt["id"])

    assert result["ok"] is True
    assert result["evidence_id"]
    assert result["verdict"] == "computed"
    call = database.attempt_history(problem["id"])[0]["tool_calls"][0]
    assert call["tool_name"] == "derive"
    assert call["arguments"]["netlist"].startswith("R1 1 0")


def test_unknown_attempt_fails_before_the_tool_runs(tmp_path, monkeypatch):
    database, problem, _ = _attempt(tmp_path, monkeypatch)

    result = server_module.check_equivalence("a+b", "b+a", attempt_id="nope")

    assert result["ok"] is False
    assert result["error"] == "storage_error"
    with database._connect() as connection:
        assert connection.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 0


def test_calls_without_attempt_id_record_nothing(tmp_path, monkeypatch):
    database, problem, _ = _attempt(tmp_path, monkeypatch)

    result = server_module.check_equivalence("a+b", "b+a")

    assert result["ok"] is True
    assert "evidence_id" not in result and "verdict" not in result
    with database._connect() as connection:
        assert connection.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 0


def test_a_storage_failure_keeps_the_result_and_warns(tmp_path, monkeypatch):
    database, problem, attempt = _attempt(tmp_path, monkeypatch)

    def refuse(*args, **kwargs):
        raise StorageError("disk is full")

    monkeypatch.setattr(server_module.CommandCenterDB, "record_tool_call", refuse)
    result = server_module.check_equivalence("a+b", "b+a", attempt_id=attempt["id"])

    assert result["ok"] is True and result["equivalent"] is True
    assert "disk is full" in result["evidence_warning"]
```

`StorageError` and `CommandCenterDB` are already imported in `server.py`; import `StorageError` into the test module from `circuit_mcp.storage`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_server.py -k "attempt_id or unknown_attempt or storage_failure" -q`
Expected: FAIL with `TypeError: derive() got an unexpected keyword argument 'attempt_id'`.

- [ ] **Step 3: Implement the helper and thread it through**

Beside `_guarded` in `server.py`:

```python
def _recorded(name: str, attempt_id: str | None, **kwargs: Any) -> dict[str, Any]:
    """Run a verification tool, and file its evidence against an attempt.

    The attempt is looked up first: a typo should not spend 30 seconds of SymPy
    and only then fail to record. The write happens here in the parent, because
    the worker is a subprocess that exists to be killable and must not own a
    database handle.
    """
    if not attempt_id:
        return _guarded(name, **kwargs)
    database = _storage()
    try:
        database.get_attempt(attempt_id)
    except StorageError as exc:
        return _failure("storage_error", str(exc))
    started = time.monotonic()
    result = _guarded(name, **kwargs)
    duration_ms = (time.monotonic() - started) * 1000
    try:
        evidence = database.record_tool_call(name, kwargs, result, duration_ms, attempt_id)
    except StorageError as exc:
        # The student's answer outlives the bookkeeping that failed to file it.
        return {**result, "evidence_warning": str(exc)}
    return {**result, "evidence_id": evidence["id"], "verdict": evidence["verdict"]}
```

Then, for each of the five tools, add the parameter and swap the call. `derive`:

```python
def derive(
    netlist: str,
    in_pos: str | int,
    in_neg: str | int,
    out_pos: str | int,
    out_neg: str | int,
    mode: str = "finite",
    attempt_id: str | None = None,
) -> dict[str, Any]:
```

```python
    return _recorded(
        "derive",
        attempt_id,
        netlist=netlist,
        in_pos=in_pos,
        in_neg=in_neg,
        out_pos=out_pos,
        out_neg=out_neg,
        mode=mode,
    )
```

`check_equivalence`:

```python
def check_equivalence(expr_a: str, expr_b: str, attempt_id: str | None = None) -> dict[str, Any]:
    ...
    return _recorded("check_equivalence", attempt_id, expr_a=expr_a, expr_b=expr_b)
```

`check_derivation`:

```python
def check_derivation(
    steps: list[str], truth: str, parameters: dict[str, float] | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    ...
    return _recorded(
        "check_derivation", attempt_id, steps=list(steps), truth=truth,
        parameters=parameters or {},
    )
```

`check_setup`:

```python
def check_setup(
    netlist: str, equations: list[str], unknowns: list[str],
    attempt_id: str | None = None,
) -> dict[str, Any]:
    ...
    return _recorded(
        "check_setup",
        attempt_id,
        netlist=netlist,
        equations=list(equations),
        unknowns=list(unknowns),
    )
```

`simulate_spice`:

```python
def simulate_spice(
    netlist: str, analysis: str, outputs: list[str] | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    ...
    return _recorded(
        "simulate_spice", attempt_id, netlist=netlist, analysis=analysis, outputs=outputs or []
    )
```

Add one line to each of the five docstrings, at the end:

```
    Pass ``attempt_id`` to file this check against an attempt; it then appears
    in ``attempt_history`` with its verdict.
```

In `CLAUDE.md`, extend workflow step 7 so the tools are told what to pass:

```
7. Use `check_setup` for circuit laws, `check_derivation` for ordered algebra,
   and `derive` only as the ground-truth oracle needed for checking. Once the
   interpretation is confirmed, call `attempt_create` and pass that
   `attempt_id` to every check, so the problem board records what was verified
   rather than leaving the evidence in this chat.
```

In `README.md`, add the same sentence wherever the verification tools are listed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_server.py -q`
Expected: PASS, including the existing `derive`/`check_*` tests, which call these tools positionally and must be unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/server.py tests/test_server.py CLAUDE.md README.md
git commit -m "Let a verification tool file its evidence against an attempt"
```

---

### Task 4: Count checks on the board

**Files:**
- Modify: `src/circuit_mcp/storage.py:680` (`list_problems`), `:754` (`course_progress`), `src/circuit_mcp/static/app.js` (`loadProblems`)
- Modify: `docs/storage.md:26`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: the `verdict` column from Task 1.
- Produces: each problem dict from `list_problems` gains `checks: dict[str, int]` keyed by verdict; `course_progress()` gains a top-level `checks: dict[str, int]`.

- [ ] **Step 1: Write the failing test**

```python
def test_problem_list_counts_checks_by_verdict(tmp_path):
    db, data = database(tmp_path)
    document = add_document(db, data)
    problem = db.create_problem("Inverting gain", "op-amps", "Find vo/vi", document["id"])
    attempt = db.create_attempt(problem["id"], "student")
    db.record_tool_call("check_derivation", {}, {"ok": True, "kind": "ok"}, 1.0, attempt["id"])
    db.record_tool_call("check_setup", {}, {"ok": True, "kind": "ok"}, 1.0, attempt["id"])
    db.record_tool_call("check_setup", {}, {"ok": False, "kind": "not_satisfied"}, 1.0, attempt["id"])
    db.record_tool_call("derive", {}, {"ok": True}, 1.0, None)  # unattached: counts nowhere

    listed = db.list_problems()[0]
    assert listed["checks"] == {"pass": 2, "fail": 1}
    assert db.course_progress()["checks"] == {"pass": 2, "fail": 1, "computed": 1}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_storage.py::test_problem_list_counts_checks_by_verdict -q`
Expected: FAIL with `KeyError: 'checks'`.

- [ ] **Step 3: Implement**

In `list_problems`, after the rows are fetched inside the same connection:

```python
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM problems WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?", values).fetchall()
            problems = [dict(row) for row in rows]
            counts: dict[str, dict[str, int]] = {}
            for problem_id, verdict, count in connection.execute(
                    "SELECT a.problem_id, t.verdict, count(*) FROM tool_calls t "
                    "JOIN attempts a ON a.id=t.attempt_id WHERE t.verdict IS NOT NULL "
                    "GROUP BY a.problem_id, t.verdict"):
                counts.setdefault(problem_id, {})[verdict] = count
        for problem in problems:
            problem["checks"] = counts.get(problem["id"], {})
        return problems
```

In `course_progress`, add the query and the key:

```python
            check_rows = connection.execute(
                "SELECT verdict,count(*) count FROM tool_calls WHERE verdict IS NOT NULL GROUP BY verdict").fetchall()
```

```python
                "topics": {row["topic"]: row["count"] for row in topics},
                "checks": {row["verdict"]: row["count"] for row in check_rows}}
```

In `static/app.js`, add the helper next to `fileCard` and render it in the problem card:

```js
function checkSummary(checks){const parts=Object.entries(checks||{}).filter(([,n])=>n).map(([verdict,n])=>`${n} ${escapeHtml(verdict)}`);return parts.length?`<small>checks: ${parts.join(' · ')}</small>`:''}
```

In the `loadProblems` template string, insert `${checkSummary(x.checks)}` directly after the existing
`<small>${x.circuit_interpretation?escapeHtml(x.circuit_interpretation):'Interpretation awaiting confirmation'}</small>`.

In `docs/storage.md:26`, replace the `tool_calls` line with:

```
- `tool_calls`: bounded JSON arguments/results linked to attempts when known,
  each with the `verdict` it proved (`pass`, `fail`, `computed`, `error`).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_storage.py tests/test_web.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/storage.py src/circuit_mcp/static/app.js docs/storage.md tests/test_storage.py
git commit -m "Count each problem's checks by verdict on the board"
```

---

### Task 5: Refuse a double-recorded web call

**Files:**
- Modify: `src/circuit_mcp/web.py:580` (`run_tool`)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `_recorded` from Task 3.
- Produces: nothing new; `POST /api/tools/{name}` returns 422 when `arguments` contains `attempt_id`.

- [ ] **Step 1: Write the failing test**

In `tests/test_web.py`, following the existing client-fixture pattern in that module:

```python
def test_posting_attempt_id_inside_arguments_is_refused(client):
    response = client.post("/api/tools/check_equivalence",
                           json={"arguments": {"expr_a": "a", "expr_b": "a", "attempt_id": "x"}})
    assert response.status_code == 422
    assert "attempt_id" in response.json()["detail"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_web.py::test_posting_attempt_id_inside_arguments_is_refused -q`
Expected: FAIL — the call runs and returns 200.

- [ ] **Step 3: Implement**

In `run_tool`, immediately after the `tool is None` check:

```python
    if "attempt_id" in request.arguments:
        # The web path records the call itself; a tool that also recorded one
        # would double-count a single check on the board.
        raise HTTPException(422, "pass attempt_id beside arguments, not inside them")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_web.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite and commit**

Run: `uv run python -m pytest -q`
Expected: every test passes; the count is the previous total plus the new tests.

```bash
git add src/circuit_mcp/web.py tests/test_web.py
git commit -m "Refuse a web tool call that hides attempt_id inside its arguments"
```

---

## Self-review

- **Spec coverage:** `_recorded` and the five tools → Task 3. `verdict_for`, schema v3 and the backfill → Task 1. `record_tool_call`/`attempt_history` → Task 2. Board counts and `app.js` → Task 4. The 422 guard → Task 5. Docs are folded into Tasks 3 and 4. Every spec section has a task.
- **Placeholders:** none; every code step carries its code.
- **Type consistency:** `verdict_for(tool_name, result) -> str` is used by `record_tool_call` and `_add_tool_call_verdict` under that name in both. `_recorded(name, attempt_id, **kwargs)` keeps the same argument order at all five call sites. `checks` is a `dict[str, int]` in storage and is read as one in `app.js`.
