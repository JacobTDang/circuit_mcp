# Andrew's PrepPal macOS app: shell and core — design

*2026-09-15 · tracks #46*

## Purpose

Today the command center runs as `run_ui.py` plus a browser tab on
`localhost:2300`. Replace that with a native app someone can double-click, on
this Mac or a classmate's.

This spec covers **sub-project 1 of 5**: the app shell, the bundled Python core,
and relocatable data. Each later sub-project adds one runtime to the same bundle
and gets its own spec, plan, and PR.

## Decisions (locked)

| Topic | Choice |
|---|---|
| Name | `Andrew's PrepPal` for the `.app`, window, menus, and Dock; `PrepPal` for the bundle id, executable, and folders (no apostrophe in ids or paths) |
| Audience | Shareable with other Macs, not just this one |
| Features across the series | Core, circuit simulation, Showman visuals, handwriting OCR, iPad capture |
| Approach | Native Swift app with the runtimes inside the bundle |
| OCR packaging | Installed from a button on first use into Application Support, not shipped in the app |
| Signing | Ad-hoc signed; notarization kept as a separate build step to add later |
| Data | `~/Library/Application Support/PrepPal/` |
| Logs | `~/Library/Logs/PrepPal/`, one file per launch, last 5 kept |
| Secrets | OpenRouter key and model in the macOS Keychain, not `.env` |
| Port | Python binds a free port and reports it; not fixed at 2300 |
| Failure | Error screen with the log tail and Restart / Quit; no automatic restart loop |
| Claude Code | A menu item copies a ready-to-paste MCP config |
| Build | Swift package + `macos/build_app.sh` + a committed `uv.lock`; no new project dependencies |
| Platform | Apple silicon, macOS 14 or later |

Rejected:

- **Electron or Tauri with a PyInstaller-frozen backend.** Heavy new
  dependencies, and PyInstaller misses the dynamic imports lcapy and SymPy use.
- **A small app that installs everything on first launch.** Needs internet and a
  long first start, and installed versions drift between users.
- **OCR inside the bundle.** Works offline immediately, but makes the app about
  4 GB for a feature many users won't turn on.

## The five sub-projects

| # | Sub-project | Adds to the bundle | Approx. size |
|---|---|---|---|
| 1 | **Shell and core** (this spec) | Swift app, standalone Python 3.12, circuit_mcp and locked packages | ~400 MB |
| 2 | Circuit simulation | ngspice built without X11 | ~10 MB |
| 3 | Showman visuals | Standalone Node runtime, Showman `dist/` and runtime-only packages | ~600 MB |
| 4 | iPad capture | UxPlay, a minimal GStreamer plugin set, ffmpeg, the two Swift helpers, permission prompts | ~150–300 MB |
| 5 | Handwriting OCR | An in-app installer that downloads PyTorch, UniMERNet, and the model into Application Support | ~3 GB, outside the app |

The app works after every sub-project. A feature whose runtime isn't bundled
yet reports itself unavailable through the status endpoint, as it does today
when a runtime isn't installed.

## Measurements behind the decisions

Taken on this Mac, 2026-09-15.

| Component | Finding | Consequence |
|---|---|---|
| Python | uv's standalone CPython 3.12.13 is 73 MB and links only system libraries | Ships as-is; relocatable |
| Core packages | `.venv` is 335 MB (SciPy 83 MB, SymPy 43 MB) | Core app ~400 MB |
| Start-up | `import circuit_mcp.web` takes 1.5–2.1 s warm | Precompile bytecode; a 60 s ready timeout is generous |
| ngspice | Homebrew build links `libfftw3`, `libreadline`, and 7 X11 libraries | Sub-project 2 builds it `--without-x` |
| Node | Homebrew Node is 93 MB and links 22 Homebrew libraries | Sub-project 3 uses the nodejs.org binary |
| Showman | Runtime-only packages: 156 packages, 506 MB (full `node_modules` 585 MB) | Sub-project 3 installs production packages only |
| UxPlay | Links 8 Homebrew GStreamer libraries; GStreamer is 214 MB with 278 plugins | Sub-project 4 relocates a minimal plugin set |
| ffmpeg | Links 19 Homebrew libraries | Sub-project 4 relocates or rebuilds it |
| OCR | Model 812 MB; PyTorch wheel 127 MB; also torchvision, transformers 4.42.4, OpenCV, timm | Sub-project 5 installs on demand |
| Showman port | `ShowmanManager` defaults to port 2301 and refuses a stranger on it | Sub-project 3 passes a chosen port; the data-folder lock prevents two servers on one store |
| MCP worker | Spawned as `python -m circuit_mcp.server --worker` | Works unchanged from an installed package |
| Host check | `TrustedHostMiddleware` allows bare `127.0.0.1` and `localhost` | A free port passes the host check |

## Architecture

### Bundle layout

```text
Andrew's PrepPal.app/
  Contents/
    Info.plist
    MacOS/PrepPal                        Swift executable
    Resources/
      python/                            standalone CPython 3.12
        bin/python3
        lib/python3.12/site-packages/    circuit_mcp + locked packages, precompiled
```

Later sub-projects add `Resources/runtime/` (ngspice, Node, Showman, UxPlay).
Nothing inside the bundle is written to at run time.

### Writable locations

```text
~/Library/Application Support/PrepPal/
  command_center/        database, uploaded files, trash, server.lock   (CIRCUIT_MCP_DATA_DIR)
  showman/               rendered objects                               (sub-project 3)
  ocr/                   OCR environment and model                      (sub-project 5)
  workspace.json
~/Library/Logs/PrepPal/server-<timestamp>.log
```

### Swift units

Each unit has one job and is testable without the others.

| Unit | Job | Depends on |
|---|---|---|
| `AppDelegate` | Single instance, menus, quit handling | All units below |
| `ServerController` | Start the server, read `READY`, check health, detect exit, stop and escalate | `Process`, `URLSession` |
| `WebWindow` | One `WKWebView` window and its delegates | WebKit |
| `StatusView` | Loading screen and error screen (log tail, Open Log, Restart, Quit) | — |
| `SecretsStore` | Read and write the OpenRouter key and model in the Keychain | Security framework |
| `LogFiles` | Create the per-launch log and keep the last 5 | FileManager |
| `DataImporter` | One-time copy of an existing `command_center` folder | FileManager |
| `MCPCommand` | Build the Claude Code config JSON for the current app location | — |

### Python units

**`circuit_mcp/paths.py` (new).** One place for every path that is currently
hard-coded relative to the checkout. Each path reads its environment variable
when first used and otherwise falls back to today's repo location, so running
from a checkout does not change.

| Path | Variable | Repo default (unchanged) | Value inside the app |
|---|---|---|---|
| Data folder | `CIRCUIT_MCP_DATA_DIR` (exists) | `.local/command_center` | `Application Support/PrepPal/command_center` |
| Showman data | `CIRCUIT_MCP_SHOWMAN_DATA_DIR` (new) | `.local/showman` | `Application Support/PrepPal/showman` |
| Runtime tools | `CIRCUIT_MCP_RUNTIME_DIR` (new) | `.local/runtime` | `Application Support/PrepPal/runtime` (sub-projects 2–4 install here; `ipad_capture` writes under it, so it cannot be in the read-only bundle) |
| Workspace config | `CIRCUIT_MCP_WORKSPACE_CONFIG` (exists) | `.local/workspace.json` | `Application Support/PrepPal/workspace.json` |
| OCR Python | `CIRCUIT_MCP_OCR_PYTHON` (exists) | `.venv-ocr.nosync/bin/python` | set by sub-project 5 |
| OCR model | `CIRCUIT_MCP_OCR_MODEL` (exists) | `models/unimernet_small` | set by sub-project 5 |

Callers switched to `paths.py`: `web.py` (`DATA`, `FILES`, `INDEX`, `HISTORY`,
`TRASH`), `storage.default_data_dir`, `ipad_capture.py` (`RUNTIME` and the tools
under it), `ocr_client.py`, `showman.py` (`data_dir`), and `workspace.py`
(`DEFAULT_CONFIG`). This also fixes `web.py` ignoring `CIRCUIT_MCP_DATA_DIR`,
which `storage.py` already honors.

**`circuit_mcp/app_server.py` (new).** The server entry point the app runs:

```text
python -m circuit_mcp.app_server --data-dir <folder>
```

1. Calls `os.setpgrp()`, so the server and every child it starts share one
   process group the app can stop together.
2. Sets `CIRCUIT_MCP_DATA_DIR` from `--data-dir` before importing `web`.
3. Takes an exclusive, non-blocking `fcntl.flock` on `<folder>/server.lock`. If
   the lock is held it prints `LOCKED <pid>` and exits with status 3.
4. Binds `127.0.0.1:0` itself and hands the socket to uvicorn, so there is no
   gap between choosing a port and using it.
5. After uvicorn finishes starting up, prints `READY <port>` on its own line and
   flushes.
6. On `SIGTERM`, lets uvicorn shut down gracefully so the existing lifespan stops
   UxPlay and Showman, and `atexit` stops the OCR worker.

`run_ui.py` takes the same lock on its data folder, so a checkout and the app
can never run two web servers on one database. The MCP stdio servers do not take
the lock; they already share the store with the web server safely.

**Front end.** The header's fixed `LOCAL · PORT 2300` label shows
`location.port` instead.

### Data flow at launch

```text
PrepPal (Swift)
  ├─ single-instance check ── another copy running? → activate it and exit
  ├─ create Application Support + Logs folders, rotate logs
  ├─ read OpenRouter key and model from the Keychain
  ├─ spawn  Resources/python/bin/python3 -m circuit_mcp.app_server --data-dir …
  │        env: CIRCUIT_MCP_DATA_DIR, CIRCUIT_MCP_WORKSPACE_CONFIG,
  │             CIRCUIT_MCP_SHOWMAN_DATA_DIR, OPENROUTER_API_KEY, OPENROUTER_MODEL
  │        stdout and stderr → server-<timestamp>.log (READY / LOCKED also parsed)
  ├─ wait for READY <port>          (60 s)
  ├─ poll GET /api/status until ok  (30 s)
  └─ load http://127.0.0.1:<port>/ in the window
```

## Lifecycle

- **Start.** As in the data flow above. The loading screen shows the current
  phase: starting the server, waiting for it to answer.
- **Running.** `Process.terminationHandler` watches the server. If it exits for
  any reason, the window switches to the error screen.
- **Restart.** The error screen's Restart button runs the start sequence again.
  Nothing restarts without the user choosing to.
- **Quit.** `applicationShouldTerminate` returns `.terminateLater`, sends
  `SIGTERM` to the server process, and waits up to 10 s. Anything still alive in
  the process group then gets `SIGKILL`, and the log records that the stop was
  forced. The app then finishes quitting.

## Window behavior

The page uses four browser features that a bare `WKWebView` does not handle.

| Page feature | Handling |
|---|---|
| File upload (`input type="file"`) | `WKUIDelegate.runOpenPanelWith` shows an `NSOpenPanel` |
| `confirm()` in `app.js` | `WKUIDelegate` shows a native `NSAlert` with OK and Cancel |
| Link with `target="_blank"` | Opens in the default browser with `NSWorkspace.open` |
| `localStorage` | Default persistent `WKWebsiteDataStore`; survives relaunch |

Navigation to any origin other than the server's is opened in the default
browser rather than inside the app window.

## Error handling

Every failure is shown to the user with its reason. Nothing is silently retried
or skipped.

| Condition | What the user sees | Log |
|---|---|---|
| Bundled Python missing or not executable | Error screen: "Andrew's PrepPal is damaged. Reinstall it." | Path that failed |
| `LOCKED <pid>` | Error screen: "Another Andrew's PrepPal server is using this data folder (process <pid>)." | Lock path and pid |
| No `READY` within 60 s | Error screen with the last 40 log lines | Timeout |
| `/api/status` not ok within 30 s | Error screen with the last 40 log lines | Last status response |
| Server exits while running | Error screen with exit status and the last 40 log lines | Exit status |
| Stop needed `SIGKILL` | Nothing extra; the app quits | "Server did not stop within 10 s; killed process group" |
| Keychain read fails | Settings window with the Keychain error; the server still starts without a key | Keychain status code |
| Import source is in use by another server | Alert: close the other server first; nothing is copied | Source path and lock holder |
| App data already exists when importing | Nothing extra: it is moved to `command_center.before-import-<timestamp>` and the alert names that folder | Both paths |
| App running from a translocated path | Alert: move Andrew's PrepPal to Applications before copying the MCP command | Detected path |

## Claude Code hookup

**Copy MCP Command** puts this on the clipboard, filled in for the app's current
location:

```json
{
  "mcpServers": {
    "circuit": {
      "command": "/Applications/Andrew's PrepPal.app/Contents/Resources/python/bin/python3",
      "args": ["-m", "circuit_mcp.server"],
      "env": {
        "CIRCUIT_MCP_DATA_DIR": "/Users/<user>/Library/Application Support/PrepPal/command_center",
        "CIRCUIT_MCP_SHOWMAN_DATA_DIR": "/Users/<user>/Library/Application Support/PrepPal/showman",
        "CIRCUIT_MCP_WORKSPACE_CONFIG": "/Users/<user>/Library/Application Support/PrepPal/workspace.json",
        "CIRCUIT_MCP_RUNTIME_DIR": "/Users/<user>/Library/Application Support/PrepPal/runtime",
        "CIRCUIT_MCP_OCR_PYTHON": "/Users/<user>/Library/Application Support/PrepPal/ocr/venv/bin/python",
        "CIRCUIT_MCP_OCR_MODEL": "/Users/<user>/Library/Application Support/PrepPal/ocr/models/unimernet_small"
      }
    }
  }
}
```

The path changes if the app moves, so the command is always generated, never
stored. The client gets the same folders the app's own server runs with, minus
the secrets, so a server started from this config reports what the app reports.

## Build and packaging

`macos/build_app.sh` produces `dist/Andrew's PrepPal.app` and
`dist/PrepPal-<version>.dmg`, taking the version from `pyproject.toml`.

1. `uv lock --check` fails the build if `uv.lock` is out of date.
2. Copy uv's standalone CPython 3.12 into `Resources/python`.
3. `uv export --frozen --no-dev` the locked requirements, then install them and
   the circuit_mcp wheel into the bundled Python with `uv pip install --python`.
4. Delete every `tests` and `__pycache__` folder under `site-packages`, then
   precompile the remaining packages with `python -m compileall -q`.
5. `swift build -c release` for the `macos/` package; copy the executable and
   `Info.plist` (bundle id `io.github.jacobtdang.preppal`,
   `LSMinimumSystemVersion` 14.0).
6. Sign step: `codesign --force --deep --sign -`. Notarization is a later
   replacement for this one step.
7. `hdiutil create` the `.dmg`.

Recipients open the app the first time with right-click, Open. The README
documents this.

## Testing

Written test-first.

**Python (pytest).**

- `paths.py`: each path's repo default, and each environment override.
- `web.py` data folder honors `CIRCUIT_MCP_DATA_DIR`.
- `app_server.py`: prints `READY <port>` for a port that answers `/api/status`;
  a second server on the same data folder prints `LOCKED <pid>` and exits 3;
  `SIGTERM` exits cleanly and releases the lock.
- `run_ui.py` refuses a data folder whose lock is held.

**Swift (XCTest)**, against a small fake server script:

- `ServerController` parses `READY` and `LOCKED`, times out without `READY`,
  reports an unexpected exit, and escalates to `SIGKILL` when `SIGTERM` is
  ignored.
- `MCPCommand` output for a given app path.
- `LogFiles` keeps the last 5 logs.

**End-to-end script** (`macos/smoke_test.sh`): build the app, open it, wait for
`/api/status`, quit it with AppleScript, then confirm no process from the bundle
is left running.

**Manual, before merge:**

- Upload a PDF.
- Close a canvas card through the confirmation dialog.
- Open the new-tab link in the browser.
- Reload and relaunch with a card position kept in `localStorage`.
- Paste the MCP command into Claude Code and call `course_progress`.

## Non-goals (this sub-project)

- ngspice, Showman, iPad capture, and OCR runtimes (sub-projects 2–5)
- Notarization, a Developer ID, and automatic updates
- Intel Macs, Windows, and Linux
- Changes to the web UI beyond the port label
- Replacing `run_ui.py` or the repo `.venv` workflow, which keep working

## Open risks (accepted)

- **Unsigned app friction.** Recipients must right-click, Open once, and macOS
  may run the app from a translocated read-only path until it is moved to
  Applications. The translocation check above covers the MCP command.
- **Two copies of the data.** Importing copies rather than moves, so the repo
  `.local/command_center` and the app's folder can drift. That is intended; the
  import is one-time.
- **Apple silicon only.** A classmate on an Intel Mac cannot run this build.

## Implementation sketch (for the plan)

1. `paths.py` and its callers, with tests.
2. `web.py` data folder through `paths.py`; the port label.
3. `app_server.py`: process group, lock, port 0, `READY`, graceful stop; lock in `run_ui.py`.
4. `uv.lock` committed; `macos/build_app.sh` producing a runnable Python bundle.
5. Swift package: `ServerController` and `LogFiles` with XCTest and the fake server.
6. `WebWindow`, `StatusView`, menus, `SecretsStore`, `DataImporter`, `MCPCommand`.
7. Sign step, `.dmg`, `smoke_test.sh`, README section.
