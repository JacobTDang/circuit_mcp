# Attempt-linked verification: recording what a check proved

Issue #43. Verification done through the MCP tools is not recorded against the
problem it verifies. `record_tool_call` has one caller, `web.py:591`, so a
session that derives, simulates and checks its way through a homework set
leaves the board untouched: the evidence exists only in the chat transcript.

The EE 2300 Module 2 HW2 session on 2026-09-22 is the second instance. Every
one of seven problems was checked — five `simulate_spice` runs, a `derive`, a
`check_derivation`, two `check_setup` calls, three `opamp_limits` — and none of
it is attached to a problem.

## The shape of the change

An optional `attempt_id` on the five verification tools. When it is present the
call is recorded exactly as the web path records it; when it is absent the tool
behaves as it does today, down to the result keys.

### Which tools

`derive`, `check_derivation`, `check_setup`, `check_equivalence`, and
`simulate_spice`. These are the five that settle whether work is right.
`circuit_equations` is deliberately out: it reports a system, it does not judge.

### `_recorded`, in `server.py`

A helper wrapping `_guarded`:

1. With no `attempt_id`, return `_guarded(name, **kwargs)` unchanged.
2. With one, load the attempt first. An unknown id returns `storage_error`
   **before the tool runs**, so a typo cannot spend 30 s of SymPy and then
   fail to record.
3. Run `_guarded`, timing it.
4. Record from the parent process, the way `canvas_card_add` already writes to
   storage after its worker call returns. The worker is a killable subprocess
   and must not own a database handle.
5. Return the result with `evidence_id` added, matching the web path.

A storage failure after a successful run returns the result with
`evidence_warning` rather than discarding it: the student's answer is worth
more than the bookkeeping.

### The `verdict` column

`ok` does not mean the same thing across these tools:

| Tool | `ok` means | Verdict lives in |
|---|---|---|
| `check_derivation`, `check_setup` | the work passed | `ok` + `kind` |
| `check_equivalence` | the comparison ran | `equivalent` |
| `derive`, `simulate_spice` | it computed | nothing to judge |

So a reader cannot recover the verdict from `ok`. Storing it at write time is
the alternative to parsing up to 10 MB of `result_json` on every read.

`verdict_for(tool_name, result)` in `storage.py`, one pure function used by
both the writer and the migration:

- a result carrying `error` → `error`
- `check_derivation`, `check_setup` → `pass` if `ok` else `fail`
- `check_equivalence` → `pass` if `equivalent` else `fail`
- anything else that ran → `computed`

An equivalence settled by the numeric oracle records `pass`. `result_json`
keeps `oracle`, so a reader that needs to distinguish a proof from strong
evidence still can, and the solution sheet prints "numeric" beside the check.

### Schema v3

`SCHEMA_VERSION = 3`, and `MIGRATIONS` gains
`(3, "add tool_call verdict", _add_tool_call_verdict)`. The step adds the
column when it is missing, then backfills every existing row through
`verdict_for`. The baseline creates `tool_calls` with the column already
present, so a fresh store and a migrated store are identical.

### Reading it back

- `record_tool_call` computes and stores the verdict, and returns it.
- `attempt_history` returns `verdict` and `arguments` per call, not just
  `ok`. The arguments are what make a recorded check reproducible.
- `list_problems` and `course_progress` count checks by verdict, so the board
  can say `checks: 3 pass · 1 fail`.
- `static/app.js` renders that line on the problem card.

### The web path

`POST /api/tools/{name}` returns 422 when `arguments` itself contains
`attempt_id`. Otherwise the request records once through `run_tool` and once
through `_recorded`, and the board double-counts a single check.

## Testing

Tests first, and each one fails before its change exists:

- `test_derive_with_attempt_id_records_arguments_and_result` — a real RC
  derive; the stored row holds the netlist and the transfer function.
- `test_attempt_history_shows_each_check_verdict` — a sound derivation, a
  broken one, and a non-equivalent pair give `pass`, `fail`, `fail`.
- `test_unknown_attempt_fails_before_the_tool_runs` — `storage_error`, and no
  row is written.
- `test_calls_without_attempt_id_record_nothing` — result keys unchanged, no
  `evidence_id`, `tool_calls` empty.
- `test_numeric_oracle_equivalence_counts_as_pass`.
- `test_migration_3_backfills_verdicts` — a v2 store with one row per tool
  comes forward with the right verdict on each.
- `test_problem_list_counts_checks_by_verdict`.
- `test_posting_attempt_id_inside_arguments_is_refused`.

`test_migrations.py:74` expects `applied_versions == [1, 2]` and becomes
`[1, 2, 3]`.

## Documentation

CLAUDE.md steps 6–7 gain the workflow this enables: call `attempt_create`
after the student confirms the transcription, then pass that id to every check.
README and `docs/storage.md:26` describe the new column.

## Out of scope

The solution sheet itself (#44), which consumes this. Recording tools that do
not verify. Any change to what the tools compute.
