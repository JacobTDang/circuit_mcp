# MATLAB bridge — design

*2026-09-08*

## Purpose

Give a local MCP agent a thin, opt-in way to run free MATLAB code (scripts,
expressions, plots) through `circuit_mcp`, without turning the tutoring oracle
into a general MATLAB IDE.

This is not a second symbolic checker. lcapy / SymPy / ngspice remain the math
oracles for circuit grading. MATLAB is an optional side channel for coursework
or personal work that already lives in MATLAB.

## Decisions (locked)

| Topic | Choice |
|---|---|
| Job | General MATLAB coding / scripts / plots (not a bounded course-oracle API) |
| Placement | Inside `circuit_mcp` (not a separate MathWorks MCP for v1) |
| Tool surface | `matlab_status` + `matlab_eval` only |
| Session | One persistent `matlab.engine` session; workspace reused across calls |
| Figures | Best-effort: after eval, export current figure to PNG and return as MCP image |
| Enablement | Explicit env opt-in, same pattern as VISA instruments |

Rejected for v1: shelling `matlab -batch` per call (breaks persistence); bundling
or proxying the official MathWorks MATLAB MCP (too heavy for two tools); Octave
fallback; broad workspace/docs/async tool suites.

## Architecture

```text
Agent (MCP client)
  └── circuit_mcp (stdio)
        ├── symbolic worker (unchanged — fork isolation for lcapy/SymPy)
        ├── instruments (opt-in VISA)
        └── matlab bridge (NEW, opt-in)
              └── one persistent matlab.engine session
                    ├── eval → stdout / ans text
                    └── after eval → export current figure → PNG → ImageContent
```

- New module: `src/circuit_mcp/matlab_bridge.py` — engine lifecycle, eval,
  figure export, bounds, enablement checks.
- `src/circuit_mcp/server.py` — register the two tools; do **not** run MATLAB
  inside the fork-based symbolic worker (Engine + `fork` is unsafe / brittle).
- Soft dependency: the MCP server must start even if `matlab.engine` is missing
  or MATLAB is not installed. Tools report a clear error instead.
- Process placement: the Engine lives in the **main MCP process**, lazy-started
  on the first successful `matlab_eval` after enablement.

## Tool contracts

### `matlab_status`

Always callable (no enablement required for the report itself).

Returns a structured dict including at least:

- `ok` — probe succeeded at the status layer
- `enabled` — whether `CIRCUIT_MCP_ENABLE_MATLAB` is exactly `1`
- `engine_importable` — whether `import matlab.engine` works
- `session_alive` — whether a persistent engine is currently held
- `matlab_version` — string if known/warm; omit or null if not started
- `note` — human guidance when disabled or Engine missing

Status must **not** start MATLAB. Starting is reserved for `matlab_eval`.

### `matlab_eval`

Requires `CIRCUIT_MCP_ENABLE_MATLAB=1`.

| Arg | Type | Notes |
|---|---|---|
| `code` | string | MATLAB to evaluate |
| `timeout_s` | number (optional) | Wall-clock bound; clamped to 5–120 inclusive; default **30** |

Returns (MCP tool result):

- Structured / text content: `ok`, captured stdout, `ans` representation when
  available, and notes (e.g. figure export skipped or failed).
- Optional `ImageContent`: PNG of the current figure when export succeeds.

Workspace persists across calls. Reset is intentional via the agent sending
`clear` / `clear all` / `close all` as code — no dedicated reset tool in v1.

When disabled, missing Engine, or MATLAB unavailable: fail with a stable error
shape consistent with other opt-in tools (e.g. instruments), not a traceback.

## Lifecycle

1. Server starts; bridge holds no engine.
2. First enabled `matlab_eval`: import Engine, `start_matlab()`, store globally.
3. Later calls reuse the same engine and workspace.
4. On MCP process exit: best-effort `quit` / drop reference.

Concurrent `matlab_eval` calls must be serialized (lock). The MCP SDK may
dispatch sync tools on a thread pool; MATLAB Engine is not assumed reentrant.

## Safety and bounds

Arbitrary MATLAB is intentional for this feature. Treat it like giving the agent
the MATLAB desktop on this machine.

Hard requirements:

- Opt-in env var: `CIRCUIT_MCP_ENABLE_MATLAB=1` (unset or any other value → denied).
- Max `code` length: **100_000** characters.
- Hard wall-clock timeout on each eval: clamp **5–120** s; default **30**.
  Prefer `engine.eval(..., background=True)` (or equivalent Future) and wait with
  the timeout; on expiry cancel/abandon that future and return a timeout error
  without tearing down the whole MCP process. If the Engine build cannot cancel
  cleanly, still return timeout to the agent and leave a note that the session
  may need `clear`/`close all` afterward.
- Max captured stdout size: **1_000_000** characters; truncate with a note.
- Figures: **one** current figure; max PNG size **5_000_000** bytes; skip with a
  note if oversized.

Out of scope for v1: OS-level sandboxing, allow-lists of MATLAB builtins, remote
MATLAB, multi-user session isolation.

Document in README: local trusted use only; the agent can run anything MATLAB can
(`system`, file deletes, network, etc.).

## Figure export

After a successful eval:

1. Detect whether a figure exists (e.g. current figure handle nonempty).
2. Export to a temporary PNG via `exportgraphics` when available, else `print`.
3. Read bytes; attach as MCP image content; delete the temp file.
4. If code succeeded but export failed: keep text result, add a note; do not
   convert a successful eval into a hard failure solely because of export.

No figure → text-only result.

## Configuration

| Variable | Meaning |
|---|---|
| `CIRCUIT_MCP_ENABLE_MATLAB` | Must be exactly `1` to allow `matlab_eval` |

Optional follow-ups (not required for v1): MATLAB binary path hints, headless
startup flags. Prefer Engine defaults first.

`matlabengine` is **not** a hard install requirement for the core tutoring stack.
Document how to install the Engine package matching the local MATLAB release.

## Testing

- Unit tests with the engine mocked: disabled-by-default, enablement gate,
  timeout/length bounds, stdout truncation, figure success and figure-failure
  paths that still return text.
- No CI job that requires a MATLAB license.
- Optional live test (skipped unless env + MATLAB present) for a smoke eval.

## Non-goals (v1)

- Course-specific MATLAB oracles (`tf`, `bode` wrappers as separate tools)
- Official MathWorks MCP embedded or proxied
- Octave compatibility layer
- Async job IDs / engine pools
- Workspace get/set/list tools beyond what `matlab_eval` already allows
- Writing figures into the command-center library automatically

## Implementation sketch (for the plan)

1. Add `matlab_bridge.py` with enablement, lazy engine, locked eval, figure export.
2. Register `matlab_status` / `matlab_eval` on the MCP server; wire image return
   like existing capture tools.
3. Tests with mocks; README + env table row.
4. Manual smoke on a machine with MATLAB + matching `matlabengine`.

## Open risks (accepted)

- Destructive agent MATLAB is possible once enabled — mitigated by opt-in + docs.
- `matlabengine` version must match the installed MATLAB — status must surface
  import/start failures clearly.
- A long eval can block the main MCP process until timeout — mitigated by hard
  timeout and a call lock, not by the symbolic fork worker.

## Revisions after review (2026-09-08)

- **Timeout recovery.** MATLAB's pid is captured via `feature('getpid')` at start;
  on timeout the future is cancelled, and if it is not cancelled within 2 s the
  pid is killed and the engine dropped so the next call starts fresh. Reason:
  `FutureResult.cancel()` cannot interrupt uninterruptible operations, and a
  busy single session would block every later call, including any `clear`.
- **Output via `evalc` with `background=True`** instead of stdout kwargs plus
  reading `ans`. Reason: one call captures printed output and the `ans` display,
  and `ans` marshalling fails for structs and objects.
- **Engine start via `start_matlab(background=True)`** with its own 90 s bound,
  separate from the eval timeout. Reason: a license stall fails cleanly instead
  of consuming the first eval's budget. Measured cold start on this machine: 4.3 s.
- **Figures invisible by default** via `set(groot,'defaultFigureVisible','off')`;
  detection via `get(groot,'CurrentFigure')`, never `gcf`. Reason: an engine
  session otherwise opens a desktop window per plot, and `gcf` creates a figure.
- **A failed figure probe or a failed version probe is reported** (a note, or a
  start failure), never silently ignored. Reason: a broken Engine must not look
  like a successful text-only eval or a warm session.
- **Enablement guidance.** Do not put `CIRCUIT_MCP_ENABLE_MATLAB=1` in
  `.mcp.json`; set it only in the shell of a session that needs MATLAB. Reason:
  this server ingests untrusted OCR and documents, an injected instruction
  becomes `system()` through `matlab_eval`, and MCP tools get no per-call
  approval.

### Alternative considered

The official MathWorks
[matlab-mcp-core-server](https://github.com/matlab/matlab-mcp-core-server)
covers the same job with no code in this repository. This in-house bridge is
justified only if a `circuit_mcp` integration such as canvas cards or library
figures follows.
