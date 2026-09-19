# Andrew's PrepPal macOS App: Shell and Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `Andrew's PrepPal.app`, a native macOS app that starts the command center's Python server itself and shows the desk in its own window, with the Python core bundled inside so it runs on another Mac.

**Architecture:** The Python side gains `paths.py`, one owner for every writable or runtime location with environment overrides, and `app_server.py`. `app_server.py` locks the data folder, binds a free loopback port, prints `READY <port>`, and shuts down gracefully on `SIGTERM`. A Swift package holds `PrepPalCore`, a testable library covering the server protocol, the process controller, health checks, logs, the Keychain, data import, and the MCP command. It also holds a thin `PrepPal` executable with the window, menus, and status screens. `macos/build_app.sh` copies uv's standalone CPython into the bundle, installs the locked packages, compiles the Swift app, ad-hoc signs it, and makes a `.dmg`.

**Tech Stack:** Python 3.12, FastAPI, uvicorn 0.52, pytest; Swift 5.10 language mode on the Swift 6.3 toolchain, AppKit, WebKit, Security, XCTest; uv 0.11 for locking, export, and install; `codesign`, `hdiutil`.

**Spec:** `docs/superpowers/specs/2026-09-15-macos-app-shell-design.md`

## Global Constraints

- **Platform:** Apple silicon only, macOS 14.0 or later (`LSMinimumSystemVersion` 14.0).
- **App name:** `Andrew's PrepPal` for the `.app`, window title, menus, and Dock. Bundle id: `io.github.jacobtdang.preppal`. The executable, Swift targets, and folder names use `PrepPal`, because a bundle id cannot contain an apostrophe and shell paths stay safer without one.
- **Dependencies:** no new project dependencies. The only packaging addition is a committed `uv.lock`.
- **Data folder:** `~/Library/Application Support/PrepPal/`, with the database under `command_center/`.
- **Logs:** `~/Library/Logs/PrepPal/server-<timestamp>.log`, one per launch; keep the newest 5.
- **Server protocol:** stdout lines exactly `READY <port>` and `LOCKED <pid>`. The locked exit status is `3`.
- **Timeouts:** `READY` within 60 s; `/api/status` ok within 30 s; graceful stop 10 s, then `SIGKILL` the process group.
- **Error screen:** shows the last 40 log lines.
- **Keychain:** service `io.github.jacobtdang.preppal`, accounts `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`.
- **Test-only override:** `PREPPAL_HOME`, when set, replaces `~/Library` as the root for the app's data and logs. It exists so the smoke test never touches real user data.
- **Running from a checkout:** `run_ui.py`, `run_server.py`, and the repo `.venv` keep working with unchanged defaults.
- **Fail loud:** no swallowed exceptions and no silent fallbacks. Every failure reaches the user or the log with its reason.
- **Tests first:** write the failing test, watch it fail, implement, watch it pass. Run the full suite with `.venv/bin/python -m pytest -q` before every Python commit.
- **Commit messages:** a short sentence in the repo's style. Never mention Claude or add attribution trailers.

---

## File Structure

**Python (modify in place, keep the existing style):**

| File | Responsibility |
|---|---|
| `src/circuit_mcp/paths.py` (create) | Every repo-relative location, each with an environment override |
| `src/circuit_mcp/app_server.py` (create) | Data-folder lock, loopback port, `READY` / `LOCKED`, graceful stop |
| `src/circuit_mcp/storage.py` (modify) | `default_data_dir` delegates to `paths` |
| `src/circuit_mcp/web.py` (modify) | `DATA` from `paths` |
| `src/circuit_mcp/workspace.py` (modify) | `config_path` from `paths` |
| `src/circuit_mcp/ipad_capture.py` (modify) | `RUNTIME` from `paths` |
| `src/circuit_mcp/ocr_client.py` (modify) | OCR Python and model from `paths` |
| `src/circuit_mcp/showman.py` (modify) | Default `data_dir` from `paths` |
| `run_ui.py` (modify) | Takes the same data-folder lock |
| `src/circuit_mcp/static/index.html`, `static/app.js` (modify) | Port label shows the real port |
| `tests/test_paths.py`, `tests/test_app_server.py` (create) | Tests for the two new modules |
| `tests/test_web.py`, `tests/test_showman.py` (modify) | Port label and Showman data folder |

**Swift package `macos/`:**

| File | Responsibility |
|---|---|
| `macos/Package.swift` | Library `PrepPalCore`, executable `PrepPal`, tests |
| `Sources/PrepPalCore/ServerLine.swift` | Parse `READY <port>` / `LOCKED <pid>` |
| `Sources/PrepPalCore/AppLocations.swift` | Data, logs, and bundled-Python locations; translocation check |
| `Sources/PrepPalCore/LogFiles.swift` | Create the per-launch log, keep the newest 5, read the tail |
| `Sources/PrepPalCore/ServerController.swift` | Spawn, wait for `READY`, watch exit, stop and escalate |
| `Sources/PrepPalCore/HealthCheck.swift` | Poll `/api/status` until ok |
| `Sources/PrepPalCore/SecretsStore.swift` | Keychain read and write |
| `Sources/PrepPalCore/DataImporter.swift` | One-time copy of an existing `command_center` |
| `Sources/PrepPalCore/MCPCommand.swift` | Claude Code config JSON |
| `Sources/PrepPal/main.swift`, `AppDelegate.swift`, `WebWindow.swift`, `StatusView.swift` | The app itself |
| `Tests/PrepPalCoreTests/*.swift`, `Tests/PrepPalCoreTests/Fixtures/fake_server.sh` | Core tests and the fake server |
| `macos/Resources/Info.plist` | Bundle metadata |
| `macos/build_app.sh`, `macos/smoke_test.sh` | Build, sign, `.dmg`; end-to-end check |

**Repo:** `uv.lock` (create), `.gitignore` and `README.md` (modify).

---

### Task 1: One module for every repo-relative path

**Files:**
- Create: `src/circuit_mcp/paths.py`
- Create: `tests/test_paths.py`
- Modify: `src/circuit_mcp/storage.py:37-39`
- Modify: `src/circuit_mcp/web.py:57-59`
- Modify: `src/circuit_mcp/workspace.py:11-18`
- Modify: `src/circuit_mcp/ipad_capture.py:18-19`
- Modify: `src/circuit_mcp/ocr_client.py:20,75-88`
- Modify: `src/circuit_mcp/showman.py:76`
- Modify: `tests/test_showman.py` (add one test)

**Interfaces:**
- Consumes: nothing.
- Produces: in `circuit_mcp.paths`:
  - `REPO_ROOT: Path`
  - `data_dir() -> Path`
  - `showman_data_dir() -> Path`
  - `runtime_dir() -> Path`
  - `workspace_config() -> Path`
  - `ocr_python() -> Path`
  - `ocr_model() -> Path`

  Each reads its variable at call time. An empty variable raises `ValueError("<NAME> is set but empty")`. Module constants in `web.py` and `ipad_capture.py` are computed at import, so a process must set the variables before importing those modules. Task 3 relies on this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paths.py`:

```python
"""Every location the command center reads or writes resolves through circuit_mcp.paths."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from circuit_mcp import paths

VARIABLES = (
    "CIRCUIT_MCP_DATA_DIR", "CIRCUIT_MCP_SHOWMAN_DATA_DIR", "CIRCUIT_MCP_RUNTIME_DIR",
    "CIRCUIT_MCP_WORKSPACE_CONFIG", "CIRCUIT_MCP_OCR_PYTHON", "CIRCUIT_MCP_OCR_MODEL",
)


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_repo_defaults_are_the_locations_used_before_this_module_existed():
    root = paths.REPO_ROOT
    assert (root / "pyproject.toml").is_file()
    assert paths.data_dir() == (root / ".local" / "command_center").resolve()
    assert paths.showman_data_dir() == (root / ".local" / "showman").resolve()
    assert paths.runtime_dir() == (root / ".local" / "runtime").resolve()
    assert paths.workspace_config() == (root / ".local" / "workspace.json").resolve()
    assert paths.ocr_python() == (root / ".venv-ocr.nosync" / "bin" / "python").absolute()
    assert paths.ocr_model() == (root / "models" / "unimernet_small").resolve()


@pytest.mark.parametrize("name, location", [
    ("CIRCUIT_MCP_DATA_DIR", paths.data_dir),
    ("CIRCUIT_MCP_SHOWMAN_DATA_DIR", paths.showman_data_dir),
    ("CIRCUIT_MCP_RUNTIME_DIR", paths.runtime_dir),
    ("CIRCUIT_MCP_WORKSPACE_CONFIG", paths.workspace_config),
    ("CIRCUIT_MCP_OCR_MODEL", paths.ocr_model),
])
def test_each_location_follows_its_environment_variable(tmp_path, monkeypatch, name, location):
    monkeypatch.setenv(name, str(tmp_path / "moved"))
    assert location() == (tmp_path / "moved").resolve()


def test_an_empty_variable_is_refused_by_name(monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_DATA_DIR", "")
    with pytest.raises(ValueError, match="CIRCUIT_MCP_DATA_DIR is set but empty"):
        paths.data_dir()


def test_the_ocr_python_keeps_its_venv_symlink_unresolved(tmp_path, monkeypatch):
    base = tmp_path / "base" / "python3.12"
    base.parent.mkdir()
    base.write_text("")
    link = tmp_path / "venv" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(base)
    monkeypatch.setenv("CIRCUIT_MCP_OCR_PYTHON", str(link))
    assert paths.ocr_python() == link


def test_a_relative_ocr_python_is_taken_relative_to_the_repo(monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_OCR_PYTHON", "envs/ocr/bin/python")
    assert paths.ocr_python() == paths.REPO_ROOT / "envs" / "ocr" / "bin" / "python"


def test_the_web_layer_and_storage_both_use_the_data_folder_variable(tmp_path):
    """web.DATA is computed at import, so this runs in a fresh interpreter."""
    target = tmp_path / "elsewhere"
    env = {**os.environ, "CIRCUIT_MCP_DATA_DIR": str(target),
           "PYTHONPATH": str(paths.REPO_ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, "-c",
         "from circuit_mcp import storage, web; print(web.DATA); print(storage.default_data_dir())"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == [str(target.resolve())] * 2


def test_the_runtime_tools_follow_the_runtime_variable(tmp_path):
    env = {**os.environ, "CIRCUIT_MCP_RUNTIME_DIR": str(tmp_path / "runtime"),
           "PYTHONPATH": str(paths.REPO_ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, "-c", "from circuit_mcp import ipad_capture as c; print(c.UXPLAY); print(c.USB_CAPTURE)"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    runtime = (tmp_path / "runtime").resolve()
    assert completed.stdout.split() == [str(runtime / "uxplay" / "bin" / "uxplay"),
                                        str(runtime / "bin" / "ipad_usb_capture")]
```

Append to `tests/test_showman.py`, directly after `test_data_dir_does_not_depend_on_the_checkout_location`:

```python
def test_data_dir_follows_the_showman_data_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_SHOWMAN_DATA_DIR", str(tmp_path / "showman-data"))
    assert ShowmanManager(port=32996).data_dir == (tmp_path / "showman-data").resolve()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_paths.py tests/test_showman.py::test_data_dir_follows_the_showman_data_variable -q`

Expected: collection fails with `ImportError: cannot import name 'paths' from 'circuit_mcp'`.

- [ ] **Step 3: Create `paths.py`**

Create `src/circuit_mcp/paths.py`:

```python
"""Every location the command center reads or writes outside its own package.

Running from a checkout keeps the historical defaults under the repository. The
macOS app, and anyone else who needs to relocate state, sets the matching
environment variable. Each function reads its variable when called; modules that
turn a location into a constant at import time must be imported after the
variable is set.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _override(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None and not value.strip():
        raise ValueError(f"{name} is set but empty")
    return value


def _location(name: str, default: Path) -> Path:
    value = _override(name)
    return (Path(value).expanduser() if value else default).resolve()


def data_dir() -> Path:
    return _location("CIRCUIT_MCP_DATA_DIR", REPO_ROOT / ".local" / "command_center")


def showman_data_dir() -> Path:
    return _location("CIRCUIT_MCP_SHOWMAN_DATA_DIR", REPO_ROOT / ".local" / "showman")


def runtime_dir() -> Path:
    return _location("CIRCUIT_MCP_RUNTIME_DIR", REPO_ROOT / ".local" / "runtime")


def workspace_config() -> Path:
    return _location("CIRCUIT_MCP_WORKSPACE_CONFIG", REPO_ROOT / ".local" / "workspace.json")


def ocr_model() -> Path:
    return _location("CIRCUIT_MCP_OCR_MODEL", REPO_ROOT / "models" / "unimernet_small")


def ocr_python() -> Path:
    value = _override("CIRCUIT_MCP_OCR_PYTHON")
    python = Path(value).expanduser() if value else REPO_ROOT / ".venv-ocr.nosync" / "bin" / "python"
    if not python.is_absolute():
        python = REPO_ROOT / python
    # Never resolve: a venv's python is a symlink to its base interpreter, and
    # resolving it would discard the venv's site-packages.
    return python.absolute()
```

- [ ] **Step 4: Point every caller at `paths`**

In `src/circuit_mcp/storage.py`, add `from . import paths` with the other package imports, and replace `default_data_dir`:

```python
def default_data_dir() -> Path:
    return paths.data_dir()
```

In `src/circuit_mcp/web.py`, add `from . import paths` above `from .ocr_client import OCR_WORKER`, delete line 57 (`ROOT = ...`), and change line 59:

```python
DATA = paths.data_dir()
```

In `src/circuit_mcp/workspace.py`, replace lines 11-18 (`ROOT`, `DEFAULT_CONFIG`, and `config_path`) with:

```python
from . import paths


def config_path() -> Path:
    return paths.workspace_config()
```

In `src/circuit_mcp/ipad_capture.py`, add `from . import paths` after the standard-library imports, and replace lines 18-19:

```python
RUNTIME = paths.runtime_dir()
```

In `src/circuit_mcp/ocr_client.py`, add `from . import paths` after the standard-library imports, delete line 20 (`ROOT = ...`), and replace the body of `_configuration`:

```python
    def _configuration(self) -> tuple[Path, Path, str]:
        device = os.environ.get("CIRCUIT_MCP_OCR_DEVICE", "auto")
        return paths.ocr_python(), paths.ocr_model(), device
```

In `src/circuit_mcp/showman.py`, add `from . import paths` after the standard-library imports, and change line 76:

```python
        self.data_dir = Path(data_dir) if data_dir else paths.showman_data_dir()
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_paths.py tests/test_showman.py::test_data_dir_follows_the_showman_data_variable -q`

Expected: `12 passed`.

- [ ] **Step 6: Check that nothing still builds these paths itself**

Run: `grep -n "parents\[2\]" src/circuit_mcp/*.py`

Expected: two lines only, `src/circuit_mcp/paths.py` (`REPO_ROOT`) and `src/circuit_mcp/showman.py` (`PROJECT_ROOT`, still used for the `vendor/showman` source folder).

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`

Expected: every test passes, 12 more than the 686 before this task.

- [ ] **Step 8: Commit**

```bash
git add src/circuit_mcp/paths.py tests/test_paths.py src/circuit_mcp/storage.py src/circuit_mcp/web.py src/circuit_mcp/workspace.py src/circuit_mcp/ipad_capture.py src/circuit_mcp/ocr_client.py src/circuit_mcp/showman.py tests/test_showman.py
git commit -m "Resolve every repo-relative location through one paths module."
```

---

### Task 2: The header shows the port the page is served on

**Files:**
- Modify: `src/circuit_mcp/static/index.html:9` and the `app.js?v=` cache-buster
- Modify: `src/circuit_mcp/static/app.js` (append one line)
- Modify: `tests/test_web.py:61-65`

**Interfaces:**
- Consumes: nothing.
- Produces: an element `#localPort` whose text is `location.port`.

- [ ] **Step 1: Write the failing test**

In `tests/test_web.py`, inside `test_page_title_block_is_removed_and_refresh_stays_reachable`, replace:

```python
        assert "LOCAL · PORT 2300" in aside
```

with:

```python
        assert 'LOCAL · PORT <span id="localPort">' in aside
```

and add, after the line `assert "$('#refresh').onclick=refresh" in app_script`:

```python
        assert "localPort.textContent=location.port" in app_script
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web.py::test_page_title_block_is_removed_and_refresh_stays_reachable -q`

Expected: FAIL on the `localPort` assertion.

- [ ] **Step 3: Implement**

In `src/circuit_mcp/static/index.html`, change line 9 to:

```html
  <div class="local"><i></i> LOCAL · PORT <span id="localPort">2300</span></div>
```

In the same file, raise the `app.js?v=` number by one so browsers fetch the new script. For example, `app.js?v=canvas-20` becomes `app.js?v=canvas-21`.

Append this line to the end of `src/circuit_mcp/static/app.js`:

```javascript
{const localPort=$('#localPort');if(localPort)localPort.textContent=location.port||'80'}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web.py -q && node -e "new Function(require('fs').readFileSync('src/circuit_mcp/static/app.js','utf8'))"`

Expected: all `tests/test_web.py` tests pass, and `node` prints nothing, which means the script still parses.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`. Expected: every test passes.

```bash
git add src/circuit_mcp/static/index.html src/circuit_mcp/static/app.js tests/test_web.py
git commit -m "Show the port the page is actually served on."
```

---

### Task 3: A server entry point that locks its data folder and reports its port

**Files:**
- Create: `src/circuit_mcp/app_server.py`
- Create: `tests/test_app_server.py`
- Modify: `run_ui.py:33-34`

**Interfaces:**
- Consumes: `circuit_mcp.paths.data_dir()` (Task 1). `web.DATA` is computed at import, so `CIRCUIT_MCP_DATA_DIR` is set before `web` is imported.
- Produces:
  - Command: `python -m circuit_mcp.app_server --data-dir <folder>`.
  - Stdout protocol: exactly one of `READY <port>` or `LOCKED <pid>`, each on its own line and flushed.
  - Exit status: `0` after a graceful stop, `3` when the folder is locked.
  - The server calls `os.setpgrp()` first, so its pid is its process-group id. The Swift controller (Task 6) relies on this to kill the whole group.
  - Python API used by `run_ui.py`:
    - `acquire_data_lock(data_dir: Path) -> TextIO`
    - `class DataFolderLocked(RuntimeError)` with `.path: Path` and `.holder: str`
    - `EXIT_LOCKED = 3`
    - `LOCK_NAME = "server.lock"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_server.py`:

```python
"""The server entry point the macOS app runs: lock, free port, READY, graceful stop."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from circuit_mcp import paths
from circuit_mcp.app_server import EXIT_LOCKED, LOCK_NAME, DataFolderLocked, acquire_data_lock

ENV = {**os.environ, "PYTHONPATH": str(paths.REPO_ROOT / "src")}


def _launch(data_dir: Path) -> tuple[subprocess.Popen, "queue.Queue[str]"]:
    process = subprocess.Popen(
        [sys.executable, "-m", "circuit_mcp.app_server", "--data-dir", str(data_dir)],
        env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    lines: "queue.Queue[str]" = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put("")

    threading.Thread(target=pump, daemon=True).start()
    return process, lines


def _protocol_line(lines: "queue.Queue[str]", timeout: float = 60.0) -> str:
    seen: list[str] = []
    while True:
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("no READY or LOCKED within %ss; output:\n%s" % (timeout, "".join(seen))) from None
        if line == "":
            raise AssertionError("server exited before READY or LOCKED; output:\n" + "".join(seen))
        seen.append(line)
        if line.startswith(("READY ", "LOCKED ")):
            return line.strip()


def _stop(process: subprocess.Popen) -> int:
    process.send_signal(signal.SIGTERM)
    return process.wait(timeout=20)


def test_ready_reports_a_port_that_serves_the_status_endpoint(tmp_path):
    process, lines = _launch(tmp_path / "data")
    try:
        line = _protocol_line(lines)
        assert line.startswith("READY ")
        port = int(line.split()[1])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=30) as response:
            assert json.load(response)["ok"] is True
        assert os.getpgid(process.pid) == process.pid
        assert (tmp_path / "data" / LOCK_NAME).read_text() == str(process.pid)
    finally:
        assert _stop(process) == 0


def test_a_graceful_stop_releases_the_lock(tmp_path):
    process, lines = _launch(tmp_path)
    assert _protocol_line(lines).startswith("READY ")
    assert _stop(process) == 0
    acquire_data_lock(tmp_path).close()


def test_a_second_server_on_a_locked_folder_says_locked_and_exits_3(tmp_path):
    held = acquire_data_lock(tmp_path)
    try:
        process, lines = _launch(tmp_path)
        assert _protocol_line(lines) == f"LOCKED {os.getpid()}"
        assert process.wait(timeout=30) == EXIT_LOCKED
    finally:
        held.close()


def test_the_lock_names_its_holder_when_refused(tmp_path):
    held = acquire_data_lock(tmp_path)
    try:
        with pytest.raises(DataFolderLocked) as refused:
            acquire_data_lock(tmp_path)
        assert refused.value.holder == str(os.getpid())
        assert refused.value.path == tmp_path / LOCK_NAME
    finally:
        held.close()


def test_run_ui_refuses_a_locked_data_folder(tmp_path):
    held = acquire_data_lock(tmp_path)
    try:
        completed = subprocess.run(
            [sys.executable, str(paths.REPO_ROOT / "run_ui.py")],
            env={**ENV, "CIRCUIT_MCP_DATA_DIR": str(tmp_path)},
            capture_output=True, text=True, timeout=60,
        )
    finally:
        held.close()
    assert completed.returncode == EXIT_LOCKED
    assert "Another server is using" in completed.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_server.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'circuit_mcp.app_server'`.

- [ ] **Step 3: Create `app_server.py`**

Create `src/circuit_mcp/app_server.py`:

```python
"""Server entry point for the Andrew's PrepPal macOS app.

    python -m circuit_mcp.app_server --data-dir <folder>

Prints exactly one protocol line on stdout: ``READY <port>`` once the server is
listening, or ``LOCKED <pid>`` when another web server holds the folder (exit
status 3). The process leads its own process group so the app can stop it and
every child it started together. SIGTERM shuts down gracefully, which runs the
web lifespan that stops UxPlay and Showman.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import socket
import sys
from pathlib import Path
from typing import TextIO

LOCK_NAME = "server.lock"
EXIT_LOCKED = 3


class DataFolderLocked(RuntimeError):
    """Another web server already holds this data folder."""

    def __init__(self, path: Path, holder: str):
        super().__init__(f"Another server is using {path.parent} (process {holder}).")
        self.path = path
        self.holder = holder


def acquire_data_lock(data_dir: Path) -> TextIO:
    """Hold an exclusive lock on ``<data_dir>/server.lock`` while the returned file stays open."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "unknown"
        handle.close()
        raise DataFolderLocked(path, holder) from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _serve(listener: socket.socket) -> None:
    import uvicorn

    from . import web  # imported only now: web.DATA reads CIRCUIT_MCP_DATA_DIR at import

    port = listener.getsockname()[1]

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(f"READY {port}", flush=True)

    ReadyServer(uvicorn.Config(web.app, lifespan="on", log_level="info")).run(sockets=[listener])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the command center for the macOS app.")
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    os.setpgrp()
    data_dir = args.data_dir.expanduser().resolve()
    os.environ["CIRCUIT_MCP_DATA_DIR"] = str(data_dir)
    try:
        lock = acquire_data_lock(data_dir)
    except DataFolderLocked as refused:
        print(f"LOCKED {refused.holder}", flush=True)
        print(refused, file=sys.stderr, flush=True)
        return EXIT_LOCKED
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        _serve(listener)
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Make `run_ui.py` take the same lock**

Replace lines 33-34 of `run_ui.py` with:

```python
if __name__ == "__main__":
    from circuit_mcp import paths
    from circuit_mcp.app_server import EXIT_LOCKED, DataFolderLocked, acquire_data_lock

    try:
        data_lock = acquire_data_lock(paths.data_dir())  # held until this process exits
    except DataFolderLocked as refused:
        print(refused, file=sys.stderr)
        sys.exit(EXIT_LOCKED)
    uvicorn.run("circuit_mcp.web:app", host="127.0.0.1", port=2300, reload=False)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app_server.py -q`

Expected: `5 passed`. Each server start takes a few seconds.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`. Expected: every test passes.

```bash
git add src/circuit_mcp/app_server.py tests/test_app_server.py run_ui.py
git commit -m "Add a server entry point that locks its data folder and reports its port."
```

---

### Task 4: Lock the dependencies and build the bundled Python runtime

**Files:**
- Create: `uv.lock` (generated by `uv lock`)
- Create: `macos/check_python_runtime.sh`
- Create: `macos/build_app.sh` (Python stage only; Task 9 adds the other stages)

**Interfaces:**
- Consumes:
  - `python -m circuit_mcp.app_server --data-dir <folder>` and its `READY <port>` line (Task 3).
- Produces:
  - `macos/build_app.sh --stage python` fills `dist/Andrew's PrepPal.app/Contents/Resources/python/`.
  - That folder holds the standalone CPython 3.12, with circuit_mcp and every locked package in its `site-packages`, precompiled.
  - The bundled interpreter is at `Contents/Resources/python/bin/python3`. Task 6 launches it and Task 7 names it in the MCP command.
  - `macos/check_python_runtime.sh [app path]` exits 0 only if that interpreter is self-contained and runs the server.

**Background for the implementer:**
- uv keeps a standalone CPython build per version under `~/.local/share/uv/python/`. It links only system libraries, so a copy works anywhere.
- uv marks that interpreter `EXTERNALLY-MANAGED`. The build deletes the marker in its private copy only, because installing packages into the copy is the point.
- `uv export --no-emit-project` writes the locked third-party requirements. circuit_mcp itself is installed separately, from a wheel built with `uv build`.

- [ ] **Step 1: Create the lock file**

Run: `uv lock`

Expected: `uv.lock` is created in the repo root, with a `Resolved N packages` message. Then run `uv lock --check`. Expected: exit 0.

- [ ] **Step 2: Write the runtime check first**

Create `macos/check_python_runtime.sh` and make it executable (`chmod +x macos/check_python_runtime.sh`):

```bash
#!/usr/bin/env bash
# Prove the app's bundled Python is self-contained and runs the command-center server.
# Usage: macos/check_python_runtime.sh [path/to/Andrew's PrepPal.app]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/dist/Andrew's PrepPal.app}"
PY="$APP/Contents/Resources/python/bin/python3"
fail() { echo "check_python_runtime: $*" >&2; exit 1; }

[ -x "$PY" ] || fail "no bundled Python at $PY"
expected_prefix="$(cd "$APP/Contents/Resources/python" && pwd -P)"

# A clean environment proves nothing leaks in from the repo .venv or PYTHONPATH.
clean=(env -i "HOME=$HOME" "PATH=/usr/bin:/bin")

prefix="$("${clean[@]}" "$PY" -c 'import os, sys; print(os.path.realpath(sys.prefix))')"
[ "$prefix" = "$expected_prefix" ] || fail "sys.prefix is $prefix, expected $expected_prefix"

"${clean[@]}" "$PY" -c '
import circuit_mcp, sys
assert "/Contents/Resources/python/" in circuit_mcp.__file__, circuit_mcp.__file__
import circuit_mcp.web  # every web dependency imports from the bundle
' || fail "circuit_mcp does not import from the bundle"

work="$(mktemp -d)"
log="$work/server.log"
"${clean[@]}" "$PY" -m circuit_mcp.app_server --data-dir "$work/data" >"$log" 2>&1 &
pid=$!

line=""
for _ in $(seq 1 120); do
  line="$(grep -m1 -E '^(READY|LOCKED) ' "$log" || true)"
  [ -n "$line" ] && break
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.5
done

if [ "${line%% *}" != "READY" ]; then
  kill -TERM "$pid" 2>/dev/null || true
  cat "$log" >&2
  fail "server did not report READY"
fi
port="${line#READY }"

if ! curl -fsS "http://127.0.0.1:$port/api/status" | grep -q '"ok":true'; then
  kill -TERM "$pid"
  cat "$log" >&2
  fail "/api/status on port $port is not ok"
fi

kill -TERM "$pid"
if wait "$pid"; then status=0; else status=$?; fi
[ "$status" -eq 0 ] || { cat "$log" >&2; fail "server exited $status after SIGTERM"; }

rm -rf "$work"
echo "check_python_runtime: bundled Python served port $port and stopped cleanly"
```

- [ ] **Step 3: Run the check to verify it fails**

Run: `macos/check_python_runtime.sh`

Expected: exit 1 with `check_python_runtime: no bundled Python at .../dist/Andrew's PrepPal.app/Contents/Resources/python/bin/python3`.

- [ ] **Step 4: Write the Python stage of the build script**

Create `macos/build_app.sh` and make it executable (`chmod +x macos/build_app.sh`):

```bash
#!/usr/bin/env bash
# Build Andrew's PrepPal.app.
# Usage: macos/build_app.sh --stage python
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Andrew's PrepPal"
APP="$ROOT/dist/$APP_NAME.app"
RESOURCES="$APP/Contents/Resources"
WORK="$ROOT/build/macos"
SITE="$RESOURCES/python/lib/python3.12/site-packages"

fail() { echo "build_app: $*" >&2; exit 1; }

stage_python() {
  command -v uv >/dev/null 2>&1 || fail "uv is required: https://docs.astral.sh/uv/"
  (cd "$ROOT" && uv lock --check) || fail "uv.lock is out of date; run 'uv lock' and commit it"

  local found base bundled
  found="$(cd /tmp && uv python find --managed-python --no-project 3.12)" \
    || fail "no uv-managed Python 3.12; run 'uv python install 3.12'"
  base="$(dirname "$(dirname "$(realpath "$found")")")"

  rm -rf "$RESOURCES/python" "$WORK"
  mkdir -p "$RESOURCES" "$WORK"
  ditto "$base" "$RESOURCES/python"
  # This copy belongs to the app, and installing packages into it is intended.
  rm -f "$RESOURCES/python/lib/python3.12/EXTERNALLY-MANAGED"

  bundled="$RESOURCES/python/bin/python3"
  [ -x "$bundled" ] || fail "the copied Python has no bin/python3"

  (cd "$ROOT" && uv export --frozen --no-dev --no-hashes --no-emit-project \
      --format requirements-txt -o "$WORK/requirements.txt")
  (cd "$ROOT" && uv build --wheel --out-dir "$WORK/wheel")
  uv pip install --python "$bundled" --no-deps -r "$WORK/requirements.txt"
  uv pip install --python "$bundled" --no-deps "$WORK"/wheel/circuit_mcp-*.whl

  find "$SITE" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +
  "$bundled" -m compileall -q "$SITE" >"$WORK/compileall.log" \
    || { cat "$WORK/compileall.log" >&2; fail "compileall reported errors (listed above)"; }
}

stage="${2:-}"
[ "${1:-}" = "--stage" ] || fail "usage: macos/build_app.sh --stage python"
case "$stage" in
  python) stage_python ;;
  *) fail "unknown stage '$stage'; expected: python" ;;
esac
echo "build_app: stage '$stage' done -> $APP"
```

If `compileall` fails only on files that are deliberately not valid Python, such as a package's syntax-error fixtures, exclude exactly those paths. Pass them with `-x '<regex>'` and add a comment naming the package. Never discard the exit status.

- [ ] **Step 5: Build the Python stage**

Run: `macos/build_app.sh --stage python`

Expected: ends with `build_app: stage 'python' done -> .../dist/Andrew's PrepPal.app`. The first run downloads build tooling for the wheel.

- [ ] **Step 6: Run the check to verify it passes**

Run: `macos/check_python_runtime.sh`

Expected: `check_python_runtime: bundled Python served port <port> and stopped cleanly`.

Then check the size: `du -sh "dist/Andrew's PrepPal.app"`. Expected: roughly 400 MB.

- [ ] **Step 7: Commit**

`dist/` and `build/` are already in `.gitignore`. Only the lock file and the two scripts are committed.

```bash
git add uv.lock macos/build_app.sh macos/check_python_runtime.sh
git commit -m "Lock dependencies and build a self-contained Python runtime for the Mac app."
```

---

### Task 5: Swift package with the server-line parser, app locations, and log files

**Files:**
- Create: `macos/Package.swift`
- Create: `macos/Sources/PrepPalCore/ServerLine.swift`
- Create: `macos/Sources/PrepPalCore/AppLocations.swift`
- Create: `macos/Sources/PrepPalCore/LogFiles.swift`
- Create: `macos/Tests/PrepPalCoreTests/ServerLineTests.swift`
- Create: `macos/Tests/PrepPalCoreTests/AppLocationsTests.swift`
- Create: `macos/Tests/PrepPalCoreTests/LogFilesTests.swift`
- Modify: `.gitignore` (add the SwiftPM build folders)

**Interfaces:**
- Consumes: the stdout protocol from Task 3 (`READY <port>`, `LOCKED <pid>`).
- Produces (module `PrepPalCore`):
  - `enum ServerLine: Equatable` with cases `.ready(port: Int)`, `.locked(pid: String)`, and `.malformed(line: String)`.
    - `static func parse(_ line: String) -> ServerLine?` returns `nil` for ordinary log output.
  - `struct AppLocations: Equatable` with:
    - `supportDirectory: URL` and `logsDirectory: URL`
    - computed `commandCenterDirectory`, `showmanDirectory`, and `workspaceConfig`
    - `static func current(environment:homeLibrary:) throws -> AppLocations`
    - `func createDirectories(fileManager:) throws`
    - `static func bundledPython(in bundle: URL) -> URL`
    - `static func isTranslocated(_ bundle: URL) -> Bool`
  - `enum AppLocationsError: Error, Equatable` with `.emptyHome`.
  - `struct LogFiles` with:
    - `init(directory: URL)` and `static let keep = 5`
    - `func startNewLog(now:fileManager:) throws -> URL`
    - `func existingLogs(fileManager:) throws -> [URL]`
    - `static func tail(of url: URL, lines: Int = 40) throws -> String`

The package uses Swift 5.10 language mode (`// swift-tools-version:5.10`) so the Swift 6 toolchain doesn't enforce strict concurrency checking on AppKit code. This task declares only the library and its tests. Task 6 adds the test fixtures, and Task 8 adds the executable.

- [ ] **Step 1: Create the package and ignore its build folders**

Create `macos/Package.swift`:

```swift
// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "PrepPal",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "PrepPalCore"),
        .testTarget(name: "PrepPalCoreTests", dependencies: ["PrepPalCore"]),
    ]
)
```

Append to `.gitignore`:

```text

# Swift package build output for the macOS app
macos/.build/
macos/.swiftpm/
```

- [ ] **Step 2: Write the failing tests**

Create `macos/Tests/PrepPalCoreTests/ServerLineTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class ServerLineTests: XCTestCase {
    func testReadyCarriesThePort() {
        XCTAssertEqual(ServerLine.parse("READY 54321\n"), .ready(port: 54321))
    }

    func testLockedCarriesTheHolder() {
        XCTAssertEqual(ServerLine.parse("LOCKED 812"), .locked(pid: "812"))
    }

    func testOrdinaryLogOutputIsNotAProtocolLine() {
        XCTAssertNil(ServerLine.parse("INFO:     Started server process [1234]"))
        XCTAssertNil(ServerLine.parse(""))
    }

    func testABrokenProtocolLineIsReportedNotIgnored() {
        XCTAssertEqual(ServerLine.parse("READY"), .malformed(line: "READY"))
        XCTAssertEqual(ServerLine.parse("READY abc"), .malformed(line: "READY abc"))
        XCTAssertEqual(ServerLine.parse("READY 70000"), .malformed(line: "READY 70000"))
        XCTAssertEqual(ServerLine.parse("LOCKED"), .malformed(line: "LOCKED"))
    }
}
```

Create `macos/Tests/PrepPalCoreTests/AppLocationsTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class AppLocationsTests: XCTestCase {
    private let library = URL(fileURLWithPath: "/Users/someone/Library", isDirectory: true)

    func testDefaultsLiveUnderTheUsersLibrary() throws {
        let locations = try AppLocations.current(environment: [:], homeLibrary: library)
        XCTAssertEqual(locations.supportDirectory.path, "/Users/someone/Library/Application Support/PrepPal")
        XCTAssertEqual(locations.logsDirectory.path, "/Users/someone/Library/Logs/PrepPal")
        XCTAssertEqual(locations.commandCenterDirectory.path, "/Users/someone/Library/Application Support/PrepPal/command_center")
        XCTAssertEqual(locations.showmanDirectory.path, "/Users/someone/Library/Application Support/PrepPal/showman")
        XCTAssertEqual(locations.workspaceConfig.path, "/Users/someone/Library/Application Support/PrepPal/workspace.json")
    }

    func testPrepPalHomeReplacesTheLibrary() throws {
        let locations = try AppLocations.current(environment: ["PREPPAL_HOME": "/tmp/preppal-test"], homeLibrary: library)
        XCTAssertEqual(locations.supportDirectory.path, "/tmp/preppal-test/Application Support/PrepPal")
        XCTAssertEqual(locations.logsDirectory.path, "/tmp/preppal-test/Logs/PrepPal")
    }

    func testAnEmptyPrepPalHomeIsRefused() {
        XCTAssertThrowsError(try AppLocations.current(environment: ["PREPPAL_HOME": ""], homeLibrary: library)) { error in
            XCTAssertEqual(error as? AppLocationsError, .emptyHome)
        }
    }

    func testCreateDirectoriesMakesBothFolders() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let locations = try AppLocations.current(environment: ["PREPPAL_HOME": root.path], homeLibrary: library)
        try locations.createDirectories()
        var isDirectory: ObjCBool = false
        XCTAssertTrue(FileManager.default.fileExists(atPath: locations.supportDirectory.path, isDirectory: &isDirectory) && isDirectory.boolValue)
        XCTAssertTrue(FileManager.default.fileExists(atPath: locations.logsDirectory.path, isDirectory: &isDirectory) && isDirectory.boolValue)
    }

    func testBundledPythonIsInsideTheApp() {
        let app = URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app", isDirectory: true)
        XCTAssertEqual(AppLocations.bundledPython(in: app).path,
                       "/Applications/Andrew's PrepPal.app/Contents/Resources/python/bin/python3")
    }

    func testTranslocationIsDetectedFromThePath() {
        XCTAssertTrue(AppLocations.isTranslocated(URL(fileURLWithPath: "/private/var/folders/x/T/AppTranslocation/ABC/d/Andrew's PrepPal.app")))
        XCTAssertFalse(AppLocations.isTranslocated(URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app")))
    }
}
```

Create `macos/Tests/PrepPalCoreTests/LogFilesTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class LogFilesTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    func testEachLaunchGetsItsOwnLogAndOnlyTheNewestFiveAreKept() throws {
        let logs = LogFiles(directory: directory)
        var created: [URL] = []
        for second in 0..<7 {
            created.append(try logs.startNewLog(now: Date(timeIntervalSince1970: 1_800_000_000 + Double(second))))
        }
        let kept = try logs.existingLogs()
        XCTAssertEqual(kept.map(\.lastPathComponent), created.suffix(5).map(\.lastPathComponent))
        XCTAssertTrue(kept.allSatisfy { $0.lastPathComponent.hasPrefix("server-") && $0.pathExtension == "log" })
    }

    func testTailReturnsTheLastFortyLines() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-x.log")
        try (1...100).map { "line \($0)" }.joined(separator: "\n").appending("\n").write(to: log, atomically: true, encoding: .utf8)
        let tail = try LogFiles.tail(of: log)
        let lines = tail.split(separator: "\n")
        XCTAssertEqual(lines.count, 40)
        XCTAssertEqual(lines.first, "line 61")
        XCTAssertEqual(lines.last, "line 100")
    }

    func testTailOfAShortLogIsTheWholeLog() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-y.log")
        try "only\ntwo".write(to: log, atomically: true, encoding: .utf8)
        XCTAssertEqual(try LogFiles.tail(of: log), "only\ntwo")
    }
}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd macos && swift test`

Expected: the build fails with errors like `cannot find 'ServerLine' in scope`. Also check that the package manifest itself is accepted.

- [ ] **Step 4: Implement the three types**

Create `macos/Sources/PrepPalCore/ServerLine.swift`:

```swift
import Foundation

/// One protocol line printed by `python -m circuit_mcp.app_server`.
public enum ServerLine: Equatable {
    case ready(port: Int)
    case locked(pid: String)
    /// Starts like a protocol line but cannot be read. Reported, never ignored.
    case malformed(line: String)

    /// Returns nil for ordinary log output.
    public static func parse(_ line: String) -> ServerLine? {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = trimmed.split(separator: " ", omittingEmptySubsequences: true)
        guard let keyword = parts.first, keyword == "READY" || keyword == "LOCKED" else { return nil }
        guard parts.count == 2 else { return .malformed(line: trimmed) }
        if keyword == "LOCKED" {
            return .locked(pid: String(parts[1]))
        }
        guard let port = Int(parts[1]), (1...65535).contains(port) else { return .malformed(line: trimmed) }
        return .ready(port: port)
    }
}
```

Create `macos/Sources/PrepPalCore/AppLocations.swift`:

```swift
import Foundation

public enum AppLocationsError: Error, Equatable {
    case emptyHome
}

/// Where the app keeps its data and logs, and where its bundled Python lives.
public struct AppLocations: Equatable {
    public static let folderName = "PrepPal"

    public let supportDirectory: URL
    public let logsDirectory: URL

    public var commandCenterDirectory: URL { supportDirectory.appendingPathComponent("command_center", isDirectory: true) }
    public var showmanDirectory: URL { supportDirectory.appendingPathComponent("showman", isDirectory: true) }
    public var workspaceConfig: URL { supportDirectory.appendingPathComponent("workspace.json") }

    /// `PREPPAL_HOME`, when set, replaces `~/Library` so tests never touch real user data.
    public static func current(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        homeLibrary: URL = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
    ) throws -> AppLocations {
        var library = homeLibrary
        if let home = environment["PREPPAL_HOME"] {
            guard !home.trimmingCharacters(in: .whitespaces).isEmpty else { throw AppLocationsError.emptyHome }
            library = URL(fileURLWithPath: home, isDirectory: true)
        }
        return AppLocations(
            supportDirectory: library.appendingPathComponent("Application Support", isDirectory: true)
                .appendingPathComponent(folderName, isDirectory: true),
            logsDirectory: library.appendingPathComponent("Logs", isDirectory: true)
                .appendingPathComponent(folderName, isDirectory: true)
        )
    }

    public func createDirectories(fileManager: FileManager = .default) throws {
        try fileManager.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
    }

    public static func bundledPython(in bundle: URL) -> URL {
        bundle.appendingPathComponent("Contents/Resources/python/bin/python3")
    }

    /// macOS runs a quarantined app from a randomized read-only copy until the user moves it.
    public static func isTranslocated(_ bundle: URL) -> Bool {
        bundle.path.contains("/AppTranslocation/")
    }
}
```

Create `macos/Sources/PrepPalCore/LogFiles.swift`:

```swift
import Foundation

public enum LogFilesError: Error, Equatable {
    case cannotCreate(URL)
    case alreadyExists(URL)
}

/// One server log per launch; the newest five are kept.
public struct LogFiles {
    public static let keep = 5
    public let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    /// Creates `server-<UTC timestamp with milliseconds>.log`, then deletes all but the newest `keep`.
    public func startNewLog(now: Date = Date(), fileManager: FileManager = .default) throws -> URL {
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyyMMdd'T'HHmmss.SSS'Z'"
        let url = directory.appendingPathComponent("server-\(formatter.string(from: now)).log")
        guard !fileManager.fileExists(atPath: url.path) else { throw LogFilesError.alreadyExists(url) }
        guard fileManager.createFile(atPath: url.path, contents: nil) else { throw LogFilesError.cannotCreate(url) }
        for old in try existingLogs(fileManager: fileManager).dropLast(Self.keep) {
            try fileManager.removeItem(at: old)
        }
        return url
    }

    /// Server logs in this directory, oldest first. Timestamped names sort chronologically.
    public func existingLogs(fileManager: FileManager = .default) throws -> [URL] {
        try fileManager.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasPrefix("server-") && $0.pathExtension == "log" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// The last `lines` lines of a log, for the error screen. Undecodable bytes are replaced, not dropped.
    public static func tail(of url: URL, lines: Int = 40) throws -> String {
        let text = String(decoding: try Data(contentsOf: url), as: UTF8.self)
        var all = text.components(separatedBy: "\n")
        if all.last == "" { all.removeLast() }
        return all.suffix(lines).joined(separator: "\n")
    }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd macos && swift test`

Expected: ends with `Executed 13 tests, with 0 failures`.

- [ ] **Step 6: Commit**

```bash
git add .gitignore macos/Package.swift macos/Sources/PrepPalCore macos/Tests/PrepPalCoreTests
git commit -m "Start the Mac app's Swift package with the server protocol, locations, and logs."
```

---

### Task 6: Server controller and health check

**Files:**
- Modify: `macos/Package.swift` (give the test target its fixtures)
- Create: `macos/Sources/PrepPalCore/ServerController.swift`
- Create: `macos/Sources/PrepPalCore/HealthCheck.swift`
- Create: `macos/Tests/PrepPalCoreTests/Fixtures/fake_server.sh`
- Create: `macos/Tests/PrepPalCoreTests/ServerControllerTests.swift`
- Create: `macos/Tests/PrepPalCoreTests/HealthCheckTests.swift`

**Interfaces:**
- Consumes: `ServerLine.parse(_:)` (Task 5) and the server protocol (Task 3).
- Produces (module `PrepPalCore`):
  - `struct ServerConfiguration`
    - fields `executable: URL`, `arguments: [String]`, `environment: [String: String]`, `logFile: URL`, `readyTimeout: TimeInterval` (default 60), `stopGracePeriod: TimeInterval` (default 10)
    - `init(executable:arguments:environment:logFile:readyTimeout:stopGracePeriod:)`
  - `enum ServerFailure: Error, Equatable` with cases:
    - `.launchFailed(String)`
    - `.locked(pid: String)`
    - `.malformedLine(String)`
    - `.readyTimeout(seconds: TimeInterval)`
    - `.exitedBeforeReady(status: Int32)`
    - `.logUnavailable(String)`
  - `enum ServerEvent: Equatable` with cases `.ready(port: Int)` and `.failed(ServerFailure)`.
  - `enum StopOutcome: Equatable` with cases `.notRunning`, `.stoppedGracefully`, and `.killed`.
  - `final class ServerController`
    - `init(configuration:)`
    - `var pid: pid_t? { get }`
    - `var onExit: ((Int32) -> Void)?` is called on the main queue when the server exits after `READY` without `stop()` having been called. The status is the exit code, or 128 plus the signal number.
    - `func start(completion: @escaping (ServerEvent) -> Void)` calls `completion` exactly once, on the main queue.
    - `@discardableResult func stop() -> StopOutcome` blocks for up to the grace period plus 5 s. It sends `SIGTERM`, and if needed follows with `SIGKILL` to the whole process group.
  - `struct HealthCheck`
    - `init(timeout: TimeInterval = 30, interval: TimeInterval = 0.5, fetch: @escaping Fetch = ...)`
    - `func waitUntilHealthy(port: Int) async throws`
    - Throws `HealthCheckError.notHealthy(lastProblem: String)`.

**Why `posix_spawn`:** Foundation's `Process` cannot put a child in its own process group. `posix_spawn` with `POSIX_SPAWN_SETPGROUP` makes the server lead a group, so `killpg` reaches everything it started. `POSIX_SPAWN_CLOEXEC_DEFAULT` stops the child from inheriting any file descriptor except stdin, stdout, and stderr. The Python server also calls `os.setpgrp()`, which is a harmless no-op once it already leads its group.

- [ ] **Step 1: Give the test target its fixtures**

In `macos/Package.swift`, replace the test target line with:

```swift
        .testTarget(name: "PrepPalCoreTests", dependencies: ["PrepPalCore"], resources: [.copy("Fixtures")]),
```

- [ ] **Step 2: Write the fake server**

Create `macos/Tests/PrepPalCoreTests/Fixtures/fake_server.sh`. Tests run it as `/bin/sh fake_server.sh <mode>`, so it doesn't need the executable bit:

```sh
#!/bin/sh
# Stands in for `python -m circuit_mcp.app_server` in ServerController tests.
case "$1" in
  ready)      echo "INFO: starting"; echo "READY 45678"; exec sleep 300 ;;
  locked)     echo "LOCKED 4242"; exit 3 ;;
  malformed)  echo "READY soon"; exec sleep 300 ;;
  silent)     exec sleep 300 ;;
  die-early)  echo "Traceback: boom" >&2; exit 1 ;;
  crash)      echo "READY 45679"; sleep 0.3; exit 7 ;;
  stubborn)   trap '' TERM; echo "READY 45680"; sleep 300 & wait ;;
  with-child) sleep 300 & echo "CHILD $!"; echo "READY 45681"; trap 'exit 0' TERM; wait ;;
  *)          echo "fake_server: unknown mode '$1'" >&2; exit 64 ;;
esac
```

- [ ] **Step 3: Write the failing tests**

Create `macos/Tests/PrepPalCoreTests/ServerControllerTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class ServerControllerTests: XCTestCase {
    private var logDirectory: URL!

    override func setUpWithError() throws {
        logDirectory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: logDirectory)
    }

    private func controller(_ mode: String, readyTimeout: TimeInterval = 10, grace: TimeInterval = 10) throws -> ServerController {
        let script = try XCTUnwrap(Bundle.module.url(forResource: "fake_server", withExtension: "sh", subdirectory: "Fixtures"))
        return ServerController(configuration: ServerConfiguration(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [script.path, mode],
            environment: ["PATH": "/usr/bin:/bin"],
            logFile: logDirectory.appendingPathComponent("server-\(mode).log"),
            readyTimeout: readyTimeout,
            stopGracePeriod: grace
        ))
    }

    private func firstEvent(of server: ServerController, timeout: TimeInterval = 15) -> ServerEvent? {
        let received = expectation(description: "start completion")
        var event: ServerEvent?
        server.start { event = $0; received.fulfill() }
        wait(for: [received], timeout: timeout)
        return event
    }

    private func log(_ mode: String) throws -> String {
        try String(contentsOf: logDirectory.appendingPathComponent("server-\(mode).log"), encoding: .utf8)
    }

    private func isAlive(_ pid: pid_t) -> Bool { kill(pid, 0) == 0 }

    func testReadyReportsThePortAndLogsTheOutput() throws {
        let server = try controller("ready")
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45678))
        XCTAssertTrue(try log("ready").contains("INFO: starting"))
        XCTAssertEqual(server.stop(), .stoppedGracefully)
    }

    func testALockedFolderIsReported() throws {
        XCTAssertEqual(firstEvent(of: try controller("locked")), .failed(.locked(pid: "4242")))
    }

    func testAMalformedProtocolLineIsReportedAndTheServerKilled() throws {
        let server = try controller("malformed")
        XCTAssertEqual(firstEvent(of: server), .failed(.malformedLine("READY soon")))
        let pid = try XCTUnwrap(server.pid)
        let gone = expectation(description: "server killed")
        DispatchQueue.global().asyncAfter(deadline: .now() + 1) { if kill(pid, 0) != 0 { gone.fulfill() } }
        wait(for: [gone], timeout: 5)
    }

    func testNoReadyWithinTheTimeoutIsReportedAndTheServerKilled() throws {
        let server = try controller("silent", readyTimeout: 1)
        XCTAssertEqual(firstEvent(of: server), .failed(.readyTimeout(seconds: 1)))
        let pid = try XCTUnwrap(server.pid)
        let gone = expectation(description: "server killed")
        DispatchQueue.global().asyncAfter(deadline: .now() + 1) { if kill(pid, 0) != 0 { gone.fulfill() } }
        wait(for: [gone], timeout: 5)
    }

    func testExitingBeforeReadyReportsTheStatusAndKeepsTheOutput() throws {
        XCTAssertEqual(firstEvent(of: try controller("die-early")), .failed(.exitedBeforeReady(status: 1)))
        XCTAssertTrue(try log("die-early").contains("Traceback: boom"))
    }

    func testAnExitAfterReadyIsReportedThroughOnExit() throws {
        let server = try controller("crash")
        let exited = expectation(description: "onExit")
        server.onExit = { status in XCTAssertEqual(status, 7); exited.fulfill() }
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45679))
        wait(for: [exited], timeout: 5)
    }

    func testStopEscalatesWhenTermIsIgnored() throws {
        let server = try controller("stubborn", grace: 1)
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45680))
        let pid = try XCTUnwrap(server.pid)
        XCTAssertEqual(server.stop(), .killed)
        XCTAssertFalse(isAlive(pid))
    }

    func testStopKillsChildrenLeftBehindInTheGroup() throws {
        let server = try controller("with-child")
        XCTAssertEqual(firstEvent(of: server), .ready(port: 45681))
        let childLine = try XCTUnwrap(try log("with-child").split(separator: "\n").first { $0.hasPrefix("CHILD ") })
        let child = try XCTUnwrap(pid_t(childLine.dropFirst("CHILD ".count)))
        XCTAssertTrue(isAlive(child))
        XCTAssertEqual(server.stop(), .killed)
        let gone = expectation(description: "child killed")
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) { if kill(child, 0) != 0 { gone.fulfill() } }
        wait(for: [gone], timeout: 5)
    }

    func testAMissingExecutableIsALaunchFailure() throws {
        let server = ServerController(configuration: ServerConfiguration(
            executable: URL(fileURLWithPath: "/nonexistent/python3"), arguments: [], environment: [:],
            logFile: logDirectory.appendingPathComponent("server-missing.log")))
        guard case .failed(.launchFailed(let reason))? = firstEvent(of: server) else {
            return XCTFail("expected a launch failure")
        }
        XCTAssertFalse(reason.isEmpty)
    }

    func testStoppingANeverStartedServerSaysSo() throws {
        XCTAssertEqual(try controller("ready").stop(), .notRunning)
    }
}
```

Create `macos/Tests/PrepPalCoreTests/HealthCheckTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class HealthCheckTests: XCTestCase {
    private func response(_ status: Int, _ body: String) -> (Data, URLResponse) {
        let url = URL(string: "http://127.0.0.1:1/api/status")!
        return (Data(body.utf8), HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }

    func testHealthyAfterTheServerStartsAnswering() async throws {
        var calls = 0
        let check = HealthCheck(timeout: 5, interval: 0.01) { url in
            calls += 1
            XCTAssertEqual(url.absoluteString, "http://127.0.0.1:5555/api/status")
            if calls < 3 { throw URLError(.cannotConnectToHost) }
            return self.response(200, #"{"ok": true, "tool_count": 50}"#)
        }
        try await check.waitUntilHealthy(port: 5555)
        XCTAssertEqual(calls, 3)
    }

    func testTimesOutNamingTheLastProblem() async {
        let check = HealthCheck(timeout: 0.2, interval: 0.05) { _ in self.response(500, #"{"detail": "boom"}"#) }
        do {
            try await check.waitUntilHealthy(port: 5556)
            XCTFail("expected a timeout")
        } catch HealthCheckError.notHealthy(let lastProblem) {
            XCTAssertEqual(lastProblem, "HTTP 500")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testOkFalseIsNotHealthy() async {
        let check = HealthCheck(timeout: 0.2, interval: 0.05) { _ in self.response(200, #"{"ok": false}"#) }
        do {
            try await check.waitUntilHealthy(port: 5557)
            XCTFail("expected a timeout")
        } catch HealthCheckError.notHealthy(let lastProblem) {
            XCTAssertEqual(lastProblem, "status did not report ok: true")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }
}
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd macos && swift test`

Expected: the build fails with errors such as `cannot find 'ServerController' in scope`.

- [ ] **Step 5: Implement `HealthCheck`**

Create `macos/Sources/PrepPalCore/HealthCheck.swift`:

```swift
import Foundation

public enum HealthCheckError: Error, Equatable {
    case notHealthy(lastProblem: String)
}

/// Polls `GET /api/status` until it answers HTTP 200 with `"ok": true`.
public struct HealthCheck {
    public typealias Fetch = (URL) async throws -> (Data, URLResponse)

    public let timeout: TimeInterval
    public let interval: TimeInterval
    private let fetch: Fetch

    public init(timeout: TimeInterval = 30, interval: TimeInterval = 0.5,
                fetch: @escaping Fetch = { try await URLSession.shared.data(from: $0) }) {
        self.timeout = timeout
        self.interval = interval
        self.fetch = fetch
    }

    public func waitUntilHealthy(port: Int) async throws {
        let url = URL(string: "http://127.0.0.1:\(port)/api/status")!
        let deadline = Date().addingTimeInterval(timeout)
        var lastProblem = "no response yet"
        repeat {
            do {
                let (data, response) = try await fetch(url)
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                if status != 200 {
                    lastProblem = "HTTP \(status)"
                } else if let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                          body["ok"] as? Bool == true {
                    return
                } else {
                    lastProblem = "status did not report ok: true"
                }
            } catch {
                lastProblem = error.localizedDescription
            }
            try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
        } while Date() < deadline
        throw HealthCheckError.notHealthy(lastProblem: lastProblem)
    }
}
```

- [ ] **Step 6: Implement `ServerController`**

Create `macos/Sources/PrepPalCore/ServerController.swift`:

```swift
import Darwin
import Foundation

public struct ServerConfiguration {
    public var executable: URL
    public var arguments: [String]
    public var environment: [String: String]
    public var logFile: URL
    public var readyTimeout: TimeInterval
    public var stopGracePeriod: TimeInterval

    public init(executable: URL, arguments: [String], environment: [String: String], logFile: URL,
                readyTimeout: TimeInterval = 60, stopGracePeriod: TimeInterval = 10) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.logFile = logFile
        self.readyTimeout = readyTimeout
        self.stopGracePeriod = stopGracePeriod
    }
}

public enum ServerFailure: Error, Equatable {
    case launchFailed(String)
    case locked(pid: String)
    case malformedLine(String)
    case readyTimeout(seconds: TimeInterval)
    case exitedBeforeReady(status: Int32)
    case logUnavailable(String)
}

public enum ServerEvent: Equatable {
    case ready(port: Int)
    case failed(ServerFailure)
}

public enum StopOutcome: Equatable {
    case notRunning
    case stoppedGracefully
    case killed
}

/// Starts the command-center server as its own process-group leader, reads its protocol
/// line, writes all of its output to the launch log, and stops it with escalation.
public final class ServerController {
    public var onExit: ((Int32) -> Void)?

    private let configuration: ServerConfiguration
    private let lock = NSLock()
    private var childPID: pid_t?
    private var resolved = false
    private var becameReady = false
    private var stopping = false
    private var exited = false
    private let exitSignal = DispatchSemaphore(value: 0)
    private let outputDrained = DispatchSemaphore(value: 0)

    public init(configuration: ServerConfiguration) {
        self.configuration = configuration
    }

    public var pid: pid_t? {
        lock.lock(); defer { lock.unlock() }
        return childPID
    }

    public func start(completion: @escaping (ServerEvent) -> Void) {
        guard FileManager.default.createFile(atPath: configuration.logFile.path, contents: nil),
              let log = try? FileHandle(forWritingTo: configuration.logFile) else {
            return resolve(.failed(.logUnavailable(configuration.logFile.path)), completion)
        }

        var fds: [Int32] = [0, 0]
        guard pipe(&fds) == 0 else {
            try? log.close()
            return resolve(.failed(.launchFailed(String(cString: strerror(errno)))), completion)
        }
        let (readEnd, writeEnd) = (fds[0], fds[1])

        let spawned: pid_t
        do {
            spawned = try spawnGroupLeader(outputFD: writeEnd)
        } catch let failure as ServerFailure {
            close(readEnd); close(writeEnd); try? log.close()
            return resolve(.failed(failure), completion)
        } catch {
            close(readEnd); close(writeEnd); try? log.close()
            return resolve(.failed(.launchFailed("\(error)")), completion)
        }
        close(writeEnd)
        lock.lock(); childPID = spawned; lock.unlock()

        Thread.detachNewThread { [self] in readOutput(from: readEnd, into: log, completion: completion) }
        Thread.detachNewThread { [self] in waitForExit(of: spawned, completion: completion) }
        DispatchQueue.global().asyncAfter(deadline: .now() + configuration.readyTimeout) { [self] in
            if resolve(.failed(.readyTimeout(seconds: configuration.readyTimeout)), completion) {
                killpg(spawned, SIGKILL)
            }
        }
    }

    @discardableResult
    public func stop() -> StopOutcome {
        lock.lock()
        guard let target = childPID, !exited else { lock.unlock(); return .notRunning }
        stopping = true
        lock.unlock()

        kill(target, SIGTERM)
        if exitSignal.wait(timeout: .now() + configuration.stopGracePeriod) == .success {
            guard killpg(target, 0) == 0 else { return .stoppedGracefully }
            killpg(target, SIGKILL)  // the server exited but left children in its group
            return .killed
        }
        killpg(target, SIGKILL)
        _ = exitSignal.wait(timeout: .now() + 5)
        return .killed
    }

    // MARK: - Internals

    private func spawnGroupLeader(outputFD: Int32) throws -> pid_t {
        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_adddup2(&actions, outputFD, 1)
        posix_spawn_file_actions_adddup2(&actions, outputFD, 2)

        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT))
        posix_spawnattr_setpgroup(&attributes, 0)

        let path = configuration.executable.path
        let argv: [UnsafeMutablePointer<CChar>?] = ([path] + configuration.arguments).map { strdup($0) } + [nil]
        let envp: [UnsafeMutablePointer<CChar>?] = configuration.environment.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer {
            argv.forEach { free($0) }
            envp.forEach { free($0) }
        }

        var spawned: pid_t = 0
        let result = posix_spawn(&spawned, path, &actions, &attributes, argv, envp)
        guard result == 0 else { throw ServerFailure.launchFailed("\(path): \(String(cString: strerror(result)))") }
        return spawned
    }

    private func readOutput(from fd: Int32, into log: FileHandle, completion: @escaping (ServerEvent) -> Void) {
        var pending = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = read(fd, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { break }
            let chunk = Data(buffer[0..<count])
            log.write(chunk)
            pending.append(chunk)
            while let newline = pending.firstIndex(of: 0x0A) {
                let line = String(decoding: pending[pending.startIndex..<newline], as: UTF8.self)
                pending.removeSubrange(pending.startIndex...newline)
                handle(line: line, completion: completion)
            }
        }
        close(fd)
        try? log.close()
        outputDrained.signal()
    }

    private func handle(line: String, completion: @escaping (ServerEvent) -> Void) {
        switch ServerLine.parse(line) {
        case .ready(let port)?:
            lock.lock(); becameReady = true; lock.unlock()
            resolve(.ready(port: port), completion)
        case .locked(let holder)?:
            resolve(.failed(.locked(pid: holder)), completion)
        case .malformed(let text)?:
            if resolve(.failed(.malformedLine(text)), completion), let target = pid {
                killpg(target, SIGKILL)
            }
        case nil:
            break
        }
    }

    private func waitForExit(of target: pid_t, completion: @escaping (ServerEvent) -> Void) {
        var raw: Int32 = 0
        while waitpid(target, &raw, 0) < 0 && errno == EINTR {}
        let signalNumber = raw & 0x7f
        let status: Int32 = signalNumber == 0 ? (raw >> 8) & 0xff : 128 + signalNumber

        // Read what the server printed last (a LOCKED line, a traceback) before deciding.
        // Children that still hold the pipe open must not block this, so the wait is bounded.
        _ = outputDrained.wait(timeout: .now() + 1)

        lock.lock()
        exited = true
        let wasReady = becameReady
        let wasStopping = stopping
        lock.unlock()
        exitSignal.signal()

        if !resolve(.failed(.exitedBeforeReady(status: status)), completion), wasReady, !wasStopping {
            DispatchQueue.main.async { [self] in onExit?(status) }
        }
    }

    /// Delivers the first event only. Returns true if this call delivered it.
    @discardableResult
    private func resolve(_ event: ServerEvent, _ completion: @escaping (ServerEvent) -> Void) -> Bool {
        lock.lock()
        guard !resolved else { lock.unlock(); return false }
        resolved = true
        lock.unlock()
        DispatchQueue.main.async { completion(event) }
        return true
    }
}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd macos && swift test`

Expected: ends with `Executed 26 tests, with 0 failures`: 13 from Task 5, plus 10 controller tests and 3 health-check tests.

- [ ] **Step 8: Commit**

```bash
git add macos/Package.swift macos/Sources/PrepPalCore/ServerController.swift macos/Sources/PrepPalCore/HealthCheck.swift macos/Tests/PrepPalCoreTests
git commit -m "Start and stop the server as a process group, and wait for it to report healthy."
```

---

### Task 7: Keychain, server environment, data import, and the MCP command

**Files:**
- Create: `macos/Sources/PrepPalCore/SecretsStore.swift`
- Create: `macos/Sources/PrepPalCore/ServerEnvironment.swift`
- Create: `macos/Sources/PrepPalCore/DataImporter.swift`
- Create: `macos/Sources/PrepPalCore/MCPCommand.swift`
- Create: `macos/Tests/PrepPalCoreTests/SecretsStoreTests.swift`
- Create: `macos/Tests/PrepPalCoreTests/ServerEnvironmentTests.swift`
- Create: `macos/Tests/PrepPalCoreTests/DataImporterTests.swift`
- Create: `macos/Tests/PrepPalCoreTests/MCPCommandTests.swift`
- Modify: `docs/superpowers/specs/2026-09-15-macos-app-shell-design.md` (lock location and import behavior)

**Interfaces:**
- Consumes: `AppLocations` (Task 5); `LOCK_NAME = "server.lock"` in the data folder (Task 3).
- Produces (module `PrepPalCore`):
  - `struct SecretsStore`
    - `static let apiKeyAccount = "OPENROUTER_API_KEY"`, `static let modelAccount = "OPENROUTER_MODEL"`
    - `init(service: String = "io.github.jacobtdang.preppal")`
    - `func read(_ account: String) throws -> String?` returns `nil` when the item is absent.
    - `func write(_ value: String, for account: String) throws`
    - `func delete(_ account: String) throws` (a missing item is not an error)
    - `func serverSecrets() throws -> [String: String]` includes only the accounts that have a value.
  - `enum SecretsStoreError: Error, Equatable`
    - `.unexpectedStatus(OSStatus, operation: String)`
    - `.notUTF8(account: String)`
  - `enum ServerEnvironment`
    - `static func variables(locations: AppLocations, secrets: [String: String], home: String = NSHomeDirectory()) -> [String: String]`
    - `static func dataVariables(locations: AppLocations) -> [String: String]`
  - `struct DataImporter`
    - `init(fileManager: FileManager = .default)`
    - `func importCommandCenter(from source: URL, to destination: URL, now: Date = Date()) throws -> ImportResult`
  - `struct ImportResult: Equatable` with `backup: URL?`.
  - `enum DataImportError: Error, Equatable` with cases:
    - `.sourceMissing(String)`
    - `.notACommandCenter(String)`
    - `.sameFolder`
    - `.sourceInUse(String)`
    - `.backupExists(String)`
  - `enum MCPCommand`
    - `static func configJSON(appBundle: URL, locations: AppLocations) throws -> String`
  - `enum MCPCommandError: Error, Equatable` with `.translocated(String)`.

**Import behavior:**
- The server creates its database on first launch, so the app's `command_center` is never empty.
- Import moves the existing app folder aside to `command_center.before-import-<UTC timestamp>` rather than refusing, and never deletes anything.
- It refuses while the source folder's `server.lock` is held, because copying a database another server is writing to can corrupt the copy.
- Task 8 stops the app's own server before importing and restarts it afterward.

- [ ] **Step 1: Write the failing tests**

Create `macos/Tests/PrepPalCoreTests/SecretsStoreTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class SecretsStoreTests: XCTestCase {
    private var store: SecretsStore!

    override func setUpWithError() throws {
        store = SecretsStore(service: "io.github.jacobtdang.preppal.tests.\(UUID().uuidString)")
    }

    override func tearDownWithError() throws {
        try store.delete(SecretsStore.apiKeyAccount)
        try store.delete(SecretsStore.modelAccount)
    }

    func testAMissingSecretReadsAsNil() throws {
        XCTAssertNil(try store.read(SecretsStore.apiKeyAccount))
    }

    func testWriteThenReadAndOverwrite() throws {
        try store.write("sk-or-first", for: SecretsStore.apiKeyAccount)
        XCTAssertEqual(try store.read(SecretsStore.apiKeyAccount), "sk-or-first")
        try store.write("sk-or-second", for: SecretsStore.apiKeyAccount)
        XCTAssertEqual(try store.read(SecretsStore.apiKeyAccount), "sk-or-second")
    }

    func testDeleteRemovesTheSecretAndToleratesAMissingOne() throws {
        try store.write("qwen/qwen3-coder:free", for: SecretsStore.modelAccount)
        try store.delete(SecretsStore.modelAccount)
        XCTAssertNil(try store.read(SecretsStore.modelAccount))
        try store.delete(SecretsStore.modelAccount)
    }

    func testServerSecretsIncludeOnlyWhatIsSet() throws {
        XCTAssertEqual(try store.serverSecrets(), [:])
        try store.write("sk-or-key", for: SecretsStore.apiKeyAccount)
        XCTAssertEqual(try store.serverSecrets(), ["OPENROUTER_API_KEY": "sk-or-key"])
    }
}
```

Create `macos/Tests/PrepPalCoreTests/ServerEnvironmentTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class ServerEnvironmentTests: XCTestCase {
    private let locations = try! AppLocations.current(environment: ["PREPPAL_HOME": "/tmp/pp"],
                                                     homeLibrary: URL(fileURLWithPath: "/unused"))

    func testTheServerGetsItsFoldersABoundedPathAndTheSecrets() {
        let variables = ServerEnvironment.variables(locations: locations,
                                                    secrets: ["OPENROUTER_API_KEY": "sk-or-key"],
                                                    home: "/Users/someone")
        XCTAssertEqual(variables["CIRCUIT_MCP_DATA_DIR"], "/tmp/pp/Application Support/PrepPal/command_center")
        XCTAssertEqual(variables["CIRCUIT_MCP_SHOWMAN_DATA_DIR"], "/tmp/pp/Application Support/PrepPal/showman")
        XCTAssertEqual(variables["CIRCUIT_MCP_WORKSPACE_CONFIG"], "/tmp/pp/Application Support/PrepPal/workspace.json")
        XCTAssertEqual(variables["OPENROUTER_API_KEY"], "sk-or-key")
        XCTAssertEqual(variables["HOME"], "/Users/someone")
        XCTAssertEqual(variables["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        XCTAssertEqual(variables["PYTHONDONTWRITEBYTECODE"], "1")
        XCTAssertNil(variables["PYTHONPATH"])
    }

    func testDataVariablesCarryNoSecrets() {
        let variables = ServerEnvironment.dataVariables(locations: locations)
        XCTAssertEqual(Set(variables.keys), ["CIRCUIT_MCP_DATA_DIR", "CIRCUIT_MCP_SHOWMAN_DATA_DIR", "CIRCUIT_MCP_WORKSPACE_CONFIG"])
    }
}
```

Create `macos/Tests/PrepPalCoreTests/DataImporterTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class DataImporterTests: XCTestCase {
    private var root: URL!
    private let fileManager = FileManager.default

    override func setUpWithError() throws {
        root = fileManager.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? fileManager.removeItem(at: root)
    }

    private func commandCenter(_ name: String, marker: String) throws -> URL {
        let folder = root.appendingPathComponent(name, isDirectory: true)
        try fileManager.createDirectory(at: folder.appendingPathComponent("files"), withIntermediateDirectories: true)
        try Data(marker.utf8).write(to: folder.appendingPathComponent("circuit_mcp.sqlite3"))
        return folder
    }

    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testImportIntoAMissingDestinationCopiesWithoutABackup() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = root.appendingPathComponent("app/command_center", isDirectory: true)
        let result = try DataImporter().importCommandCenter(from: source, to: destination, now: now)
        XCTAssertNil(result.backup)
        XCTAssertEqual(try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3")), "repo-db")
        XCTAssertTrue(fileManager.fileExists(atPath: source.appendingPathComponent("circuit_mcp.sqlite3").path))
    }

    func testExistingAppDataIsMovedAsideNotDeleted() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let destination = try commandCenter("command_center", marker: "fresh-app-db")
        let result = try DataImporter().importCommandCenter(from: source, to: destination, now: now)
        let backup = try XCTUnwrap(result.backup)
        XCTAssertEqual(backup.lastPathComponent, "command_center.before-import-20270115T080000Z")
        XCTAssertEqual(try String(contentsOf: backup.appendingPathComponent("circuit_mcp.sqlite3")), "fresh-app-db")
        XCTAssertEqual(try String(contentsOf: destination.appendingPathComponent("circuit_mcp.sqlite3")), "repo-db")
    }

    func testAFolderWithoutADatabaseIsRefused() throws {
        let empty = root.appendingPathComponent("not-a-store", isDirectory: true)
        try fileManager.createDirectory(at: empty, withIntermediateDirectories: true)
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: empty, to: root.appendingPathComponent("dest"))) {
            XCTAssertEqual($0 as? DataImportError, .notACommandCenter(empty.path))
        }
    }

    func testASourceInUseByAnotherServerIsRefused() throws {
        let source = try commandCenter("repo", marker: "repo-db")
        let lockPath = source.appendingPathComponent("server.lock").path
        try Data("777".utf8).write(to: URL(fileURLWithPath: lockPath))
        let held = open(lockPath, O_RDWR)
        XCTAssertGreaterThanOrEqual(held, 0)
        XCTAssertEqual(flock(held, LOCK_EX | LOCK_NB), 0)
        defer { close(held) }
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: root.appendingPathComponent("dest"))) {
            XCTAssertEqual($0 as? DataImportError, .sourceInUse("777"))
        }
    }

    func testImportingAFolderIntoItselfIsRefused() throws {
        let source = try commandCenter("command_center", marker: "db")
        XCTAssertThrowsError(try DataImporter().importCommandCenter(from: source, to: source)) {
            XCTAssertEqual($0 as? DataImportError, .sameFolder)
        }
    }
}
```

Create `macos/Tests/PrepPalCoreTests/MCPCommandTests.swift`:

```swift
import XCTest
@testable import PrepPalCore

final class MCPCommandTests: XCTestCase {
    private let locations = try! AppLocations.current(environment: [:], homeLibrary: URL(fileURLWithPath: "/Users/someone/Library"))

    func testTheConfigPointsClaudeCodeAtTheBundledPythonAndTheAppData() throws {
        let app = URL(fileURLWithPath: "/Applications/Andrew's PrepPal.app", isDirectory: true)
        let json = try MCPCommand.configJSON(appBundle: app, locations: locations)
        let parsed = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        let circuit = try XCTUnwrap((parsed["mcpServers"] as? [String: Any])?["circuit"] as? [String: Any])
        XCTAssertEqual(circuit["command"] as? String, "/Applications/Andrew's PrepPal.app/Contents/Resources/python/bin/python3")
        XCTAssertEqual(circuit["args"] as? [String], ["-m", "circuit_mcp.server"])
        let env = try XCTUnwrap(circuit["env"] as? [String: String])
        XCTAssertEqual(env["CIRCUIT_MCP_DATA_DIR"], "/Users/someone/Library/Application Support/PrepPal/command_center")
        XCTAssertNil(env["OPENROUTER_API_KEY"])
    }

    func testATranslocatedAppIsRefused() {
        let app = URL(fileURLWithPath: "/private/var/folders/x/T/AppTranslocation/ABC/d/Andrew's PrepPal.app")
        XCTAssertThrowsError(try MCPCommand.configJSON(appBundle: app, locations: locations)) {
            XCTAssertEqual($0 as? MCPCommandError, .translocated(app.path))
        }
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd macos && swift test`

Expected: the build fails with errors like `cannot find 'SecretsStore' in scope`.

- [ ] **Step 3: Implement the four types**

Create `macos/Sources/PrepPalCore/SecretsStore.swift`:

```swift
import Foundation
import Security

public enum SecretsStoreError: Error, Equatable {
    case unexpectedStatus(OSStatus, operation: String)
    case notUTF8(account: String)
}

/// The OpenRouter key and model, kept as generic passwords in the login Keychain.
public struct SecretsStore {
    public static let apiKeyAccount = "OPENROUTER_API_KEY"
    public static let modelAccount = "OPENROUTER_MODEL"

    public let service: String

    public init(service: String = "io.github.jacobtdang.preppal") {
        self.service = service
    }

    private func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    public func read(_ account: String) throws -> String? {
        var request = query(account)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw SecretsStoreError.unexpectedStatus(status, operation: "read \(account)") }
        guard let data = item as? Data, let value = String(data: data, encoding: .utf8) else {
            throw SecretsStoreError.notUTF8(account: account)
        }
        return value
    }

    public func write(_ value: String, for account: String) throws {
        let data = Data(value.utf8)
        let updated = SecItemUpdate(query(account) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if updated == errSecSuccess { return }
        guard updated == errSecItemNotFound else {
            throw SecretsStoreError.unexpectedStatus(updated, operation: "update \(account)")
        }
        var item = query(account)
        item[kSecValueData as String] = data
        let added = SecItemAdd(item as CFDictionary, nil)
        guard added == errSecSuccess else { throw SecretsStoreError.unexpectedStatus(added, operation: "add \(account)") }
    }

    public func delete(_ account: String) throws {
        let status = SecItemDelete(query(account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw SecretsStoreError.unexpectedStatus(status, operation: "delete \(account)")
        }
    }

    public func serverSecrets() throws -> [String: String] {
        var secrets: [String: String] = [:]
        for account in [Self.apiKeyAccount, Self.modelAccount] {
            if let value = try read(account) { secrets[account] = value }
        }
        return secrets
    }
}
```

Create `macos/Sources/PrepPalCore/ServerEnvironment.swift`:

```swift
import Foundation

/// The exact environment the server process starts with. Nothing is inherited from the app.
public enum ServerEnvironment {
    public static func dataVariables(locations: AppLocations) -> [String: String] {
        ["CIRCUIT_MCP_DATA_DIR": locations.commandCenterDirectory.path,
         "CIRCUIT_MCP_SHOWMAN_DATA_DIR": locations.showmanDirectory.path,
         "CIRCUIT_MCP_WORKSPACE_CONFIG": locations.workspaceConfig.path]
    }

    public static func variables(locations: AppLocations, secrets: [String: String],
                                 home: String = NSHomeDirectory()) -> [String: String] {
        var variables = dataVariables(locations: locations)
        variables["HOME"] = home
        variables["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        variables["LANG"] = "en_US.UTF-8"
        variables["PYTHONDONTWRITEBYTECODE"] = "1"  // the bundle is read-only and already compiled
        variables.merge(secrets) { _, secret in secret }
        return variables
    }
}
```

Create `macos/Sources/PrepPalCore/DataImporter.swift`:

```swift
import Darwin
import Foundation

public enum DataImportError: Error, Equatable {
    case sourceMissing(String)
    case notACommandCenter(String)
    case sameFolder
    case sourceInUse(String)
    case backupExists(String)
}

public struct ImportResult: Equatable {
    public let backup: URL?
}

/// One-time copy of an existing command-center folder into the app's data folder.
/// Nothing is ever deleted: existing app data is moved aside first.
public struct DataImporter {
    private let fileManager: FileManager

    public init(fileManager: FileManager = .default) {
        self.fileManager = fileManager
    }

    public func importCommandCenter(from source: URL, to destination: URL, now: Date = Date()) throws -> ImportResult {
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: source.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw DataImportError.sourceMissing(source.path)
        }
        guard fileManager.fileExists(atPath: source.appendingPathComponent("circuit_mcp.sqlite3").path) else {
            throw DataImportError.notACommandCenter(source.path)
        }
        let sourcePath = source.standardizedFileURL.resolvingSymlinksInPath().path
        let destinationPath = destination.standardizedFileURL.resolvingSymlinksInPath().path
        guard sourcePath != destinationPath, !destinationPath.hasPrefix(sourcePath + "/") else {
            throw DataImportError.sameFolder
        }
        if let holder = lockHolder(of: source) {
            throw DataImportError.sourceInUse(holder)
        }

        var backup: URL?
        if fileManager.fileExists(atPath: destination.path) {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(identifier: "UTC")
            formatter.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
            let aside = destination.deletingLastPathComponent()
                .appendingPathComponent("\(destination.lastPathComponent).before-import-\(formatter.string(from: now))", isDirectory: true)
            guard !fileManager.fileExists(atPath: aside.path) else { throw DataImportError.backupExists(aside.path) }
            try fileManager.moveItem(at: destination, to: aside)
            backup = aside
        }
        try fileManager.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try fileManager.copyItem(at: source, to: destination)
        return ImportResult(backup: backup)
    }

    /// The pid recorded in `server.lock` if another process holds that lock, else nil.
    private func lockHolder(of folder: URL) -> String? {
        let path = folder.appendingPathComponent("server.lock").path
        guard fileManager.fileExists(atPath: path) else { return nil }
        let fd = open(path, O_RDONLY)
        guard fd >= 0 else { return "unknown" }
        defer { close(fd) }
        if flock(fd, LOCK_EX | LOCK_NB) == 0 {
            flock(fd, LOCK_UN)
            return nil
        }
        let holder = (try? String(contentsOfFile: path, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        return holder?.isEmpty == false ? holder : "unknown"
    }
}
```

Create `macos/Sources/PrepPalCore/MCPCommand.swift`:

```swift
import Foundation

public enum MCPCommandError: Error, Equatable {
    case translocated(String)
}

/// The Claude Code MCP config for the app's current location. Always generated, never stored,
/// because it contains the app's path.
public enum MCPCommand {
    public static func configJSON(appBundle: URL, locations: AppLocations) throws -> String {
        guard !AppLocations.isTranslocated(appBundle) else { throw MCPCommandError.translocated(appBundle.path) }
        let config: [String: Any] = [
            "mcpServers": [
                "circuit": [
                    "command": AppLocations.bundledPython(in: appBundle).path,
                    "args": ["-m", "circuit_mcp.server"],
                    "env": ServerEnvironment.dataVariables(locations: locations),
                ],
            ],
        ]
        let data = try JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        return String(decoding: data, as: UTF8.self)
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd macos && swift test`

Expected: `Executed 39 tests, with 0 failures`.

- [ ] **Step 5: Bring the spec in line with the lock location and import behavior**

In `docs/superpowers/specs/2026-09-15-macos-app-shell-design.md`:
- In the "Writable locations" block, delete the line `  server.lock` under `PrepPal/`, and change the `command_center/` line so its description reads `database, uploaded files, trash, server.lock`.
- In the error table, replace the row that starts `| Import target not empty |` with these two rows:

```markdown
| Import source is in use by another server | Alert: close the other server first; nothing is copied | Source path and lock holder |
| App data already exists when importing | Nothing extra: it is moved to `command_center.before-import-<timestamp>` and the alert names that folder | Both paths |
```

- [ ] **Step 6: Commit**

```bash
git add macos/Sources/PrepPalCore macos/Tests/PrepPalCoreTests docs/superpowers/specs/2026-09-15-macos-app-shell-design.md
git commit -m "Keep secrets in the Keychain, import existing data safely, and build the MCP command."
```

---

### Task 8: The app: window, status screens, menus, and the bundle's app stage

**Files:**
- Modify: `macos/Package.swift` (add the executable)
- Create: `macos/Sources/PrepPal/main.swift`
- Create: `macos/Sources/PrepPal/AppDelegate.swift`
- Create: `macos/Sources/PrepPal/WebWindow.swift`
- Create: `macos/Sources/PrepPal/StatusView.swift`
- Create: `macos/Resources/Info.plist`
- Modify: `macos/build_app.sh` (add the `app` stage)

**Interfaces:**
- Consumes (all from `PrepPalCore`):
  - `AppLocations`, `LogFiles` (Task 5)
  - `ServerController`, `ServerConfiguration`, `ServerEvent`, `ServerFailure`, `StopOutcome`, `HealthCheck` (Task 6)
  - `SecretsStore`, `ServerEnvironment`, `DataImporter`, `MCPCommand` (Task 7)
- Produces:
  - `macos/build_app.sh --stage app` puts `Contents/MacOS/PrepPal` and `Contents/Info.plist` into `dist/Andrew's PrepPal.app`. It requires the `python` stage to have run first.

This code is AppKit UI, so it has no unit tests. Every rule it depends on is already tested in `PrepPalCore`. Instead, this task ends with explicit manual checks, one step each, and each check must be observed before committing.

- [ ] **Step 1: Add the executable target**

In `macos/Package.swift`, add this line to `targets`, directly after `.target(name: "PrepPalCore"),`:

```swift
        .executableTarget(name: "PrepPal", dependencies: ["PrepPalCore"]),
```

- [ ] **Step 2: Create `main.swift`**

Create `macos/Sources/PrepPal/main.swift`:

```swift
import AppKit

let application = NSApplication.shared
let appDelegate = AppDelegate()
application.delegate = appDelegate
application.setActivationPolicy(.regular)
application.run()
```

- [ ] **Step 3: Create `StatusView.swift`**

Create `macos/Sources/PrepPal/StatusView.swift`:

```swift
import AppKit

/// Covers the web view while the server starts, and explains any failure with the log tail.
final class StatusView: NSView {
    var onOpenLog: (() -> Void)?
    var onRestart: (() -> Void)?
    var onQuit: (() -> Void)?

    private let spinner = NSProgressIndicator()
    private let titleLabel = NSTextField(wrappingLabelWithString: "")
    private let detailLabel = NSTextField(wrappingLabelWithString: "")
    private let logView = NSTextView()
    private let logScroll = NSScrollView()
    private let buttonRow = NSStackView()

    init() {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        spinner.style = .spinning
        titleLabel.font = .systemFont(ofSize: 17, weight: .semibold)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.isSelectable = true

        logView.isEditable = false
        logView.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        logScroll.documentView = logView
        logScroll.hasVerticalScroller = true
        logScroll.borderType = .bezelBorder
        logView.autoresizingMask = [.width]

        let openLog = NSButton(title: "Open Log", target: self, action: #selector(openLogPressed))
        let restart = NSButton(title: "Restart", target: self, action: #selector(restartPressed))
        let quit = NSButton(title: "Quit", target: self, action: #selector(quitPressed))
        restart.keyEquivalent = "\r"
        [openLog, restart, quit].forEach(buttonRow.addArrangedSubview)

        let column = NSStackView(views: [spinner, titleLabel, detailLabel, logScroll, buttonRow])
        column.orientation = .vertical
        column.alignment = .leading
        column.spacing = 12
        column.translatesAutoresizingMaskIntoConstraints = false
        addSubview(column)
        NSLayoutConstraint.activate([
            column.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 32),
            column.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -32),
            column.centerYAnchor.constraint(equalTo: centerYAnchor),
            logScroll.widthAnchor.constraint(equalTo: column.widthAnchor),
            logScroll.heightAnchor.constraint(equalToConstant: 260),
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("StatusView is built in code")
    }

    func showLoading(_ message: String) {
        isHidden = false
        spinner.isHidden = false
        spinner.startAnimation(nil)
        titleLabel.stringValue = message
        detailLabel.isHidden = true
        logScroll.isHidden = true
        buttonRow.isHidden = true
    }

    func showError(title: String, detail: String, logTail: String?) {
        isHidden = false
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        titleLabel.stringValue = title
        detailLabel.stringValue = detail
        detailLabel.isHidden = false
        logView.string = logTail ?? ""
        logScroll.isHidden = logTail == nil
        buttonRow.isHidden = false
    }

    func hide() {
        spinner.stopAnimation(nil)
        isHidden = true
    }

    @objc private func openLogPressed() { onOpenLog?() }
    @objc private func restartPressed() { onRestart?() }
    @objc private func quitPressed() { onQuit?() }
}
```

- [ ] **Step 4: Create `WebWindow.swift`**

Create `macos/Sources/PrepPal/WebWindow.swift`:

```swift
import AppKit
import WebKit

/// The desk in a WKWebView, with the four browser features the page relies on.
final class WebWindow: NSObject, WKUIDelegate, WKNavigationDelegate {
    let webView: WKWebView
    var onLoadFailure: ((Error) -> Void)?
    private var serverRoot: URL?

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()  // persistent: localStorage survives relaunch
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.uiDelegate = self
        webView.navigationDelegate = self
    }

    func load(port: Int) {
        let root = URL(string: "http://127.0.0.1:\(port)/")!
        serverRoot = root
        webView.load(URLRequest(url: root))
    }

    func reload() {
        webView.reload()
    }

    // <input type="file">
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.begin { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    // confirm()
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    // alert()
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
        completionHandler()
    }

    // target="_blank" opens in the default browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // Anything outside the server's origin opens in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, let root = serverRoot else { return decisionHandler(.allow) }
        let sameOrigin = url.scheme == root.scheme && url.host == root.host && url.port == root.port
        if sameOrigin || ["about", "blob", "data"].contains(url.scheme ?? "") {
            return decisionHandler(.allow)
        }
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        onLoadFailure?(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        onLoadFailure?(error)
    }
}
```

- [ ] **Step 5: Create `AppDelegate.swift`**

Create `macos/Sources/PrepPal/AppDelegate.swift`:

```swift
import AppKit
import PrepPalCore

final class AppDelegate: NSObject, NSApplicationDelegate {
    static let displayName = "Andrew's PrepPal"

    private var window: NSWindow!
    private let web = WebWindow()
    private let status = StatusView()
    private let secrets = SecretsStore()
    private var locations: AppLocations?
    private var server: ServerController?
    private var logURL: URL?

    // MARK: Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        if activateAnotherRunningCopy() {
            NSApp.terminate(nil)
            return
        }
        buildMenu()
        buildWindow()
        do {
            let found = try AppLocations.current()
            try found.createDirectories()
            locations = found
        } catch {
            status.showError(title: "\(Self.displayName) cannot create its folders.", detail: "\(error)", logTail: nil)
            return
        }
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func activateAnotherRunningCopy() -> Bool {
        guard let identifier = Bundle.main.bundleIdentifier else { return false }
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
            .filter { $0 != NSRunningApplication.current }
        guard let other = others.first else { return false }
        other.activate()
        return true
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = Self.displayName
        window.setFrameAutosaveName("PrepPalMainWindow")

        let content = NSView()
        for view in [web.webView, status] as [NSView] {
            view.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(view)
            NSLayoutConstraint.activate([
                view.leadingAnchor.constraint(equalTo: content.leadingAnchor),
                view.trailingAnchor.constraint(equalTo: content.trailingAnchor),
                view.topAnchor.constraint(equalTo: content.topAnchor),
                view.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            ])
        }
        window.contentView = content

        status.onOpenLog = { [weak self] in self?.openLogs() }
        status.onRestart = { [weak self] in self?.restartServer() }
        status.onQuit = { NSApp.terminate(nil) }
        web.onLoadFailure = { [weak self] error in
            self?.showFailure("The desk could not be loaded.", detail: error.localizedDescription)
        }

        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: Server

    private func startServer() {
        guard let locations else { return }
        status.showLoading("Starting the server…")

        let python = AppLocations.bundledPython(in: Bundle.main.bundleURL)
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            status.showError(title: "\(Self.displayName) is damaged. Reinstall it.",
                             detail: "Missing or not executable: \(python.path)", logTail: nil)
            return
        }

        var serverSecrets: [String: String] = [:]
        do {
            serverSecrets = try secrets.serverSecrets()
        } catch {
            // The server still starts; Showman reports that no key is configured.
            showSettings(reason: "The Keychain could not be read, so the server started without your OpenRouter settings: \(error)")
        }

        let log: URL
        do {
            log = try LogFiles(directory: locations.logsDirectory).startNewLog()
        } catch {
            status.showError(title: "\(Self.displayName) cannot write its log.", detail: "\(error)", logTail: nil)
            return
        }
        logURL = log

        let controller = ServerController(configuration: ServerConfiguration(
            executable: python,
            arguments: ["-m", "circuit_mcp.app_server", "--data-dir", locations.commandCenterDirectory.path],
            environment: ServerEnvironment.variables(locations: locations, secrets: serverSecrets),
            logFile: log
        ))
        controller.onExit = { [weak self] exitStatus in
            self?.showFailure("The server stopped unexpectedly (exit status \(exitStatus)).", detail: "Restart to start it again.")
        }
        server = controller
        controller.start { [weak self] event in self?.handle(event) }
    }

    private func handle(_ event: ServerEvent) {
        switch event {
        case .ready(let port):
            status.showLoading("Waiting for the server to answer…")
            Task { @MainActor [weak self] in
                do {
                    try await HealthCheck().waitUntilHealthy(port: port)
                    self?.web.load(port: port)
                    self?.status.hide()
                } catch {
                    self?.showFailure("The server did not answer on port \(port).", detail: "\(error)")
                }
            }
        case .failed(let failure):
            showFailure(Self.headline(for: failure), detail: "\(failure)")
        }
    }

    static func headline(for failure: ServerFailure) -> String {
        switch failure {
        case .launchFailed: return "\(displayName) could not start its server."
        case .locked(let pid): return "Another \(displayName) server is using this data folder (process \(pid))."
        case .malformedLine: return "The server sent a startup message the app could not read."
        case .readyTimeout(let seconds): return "The server did not start within \(Int(seconds)) seconds."
        case .exitedBeforeReady(let exitStatus): return "The server stopped while starting (exit status \(exitStatus))."
        case .logUnavailable: return "\(displayName) could not write its log."
        }
    }

    private func showFailure(_ title: String, detail: String) {
        var tail: String?
        if let logURL {
            do {
                tail = try LogFiles.tail(of: logURL)
            } catch {
                tail = "The log could not be read: \(error)"
            }
        }
        status.showError(title: title, detail: detail, logTail: tail)
    }

    /// Stops the server off the main thread, then runs `then` on the main thread.
    private func stopServer(then: @escaping (StopOutcome) -> Void) {
        guard let running = server else { return then(.notRunning) }
        let log = logURL
        DispatchQueue.global().async {
            let outcome = running.stop()
            if outcome == .killed, let log {
                Self.appendToLog(log, "PrepPal: server did not stop within 10 s; killed its process group\n")
            }
            DispatchQueue.main.async { then(outcome) }
        }
    }

    private static func appendToLog(_ log: URL, _ line: String) {
        do {
            let handle = try FileHandle(forWritingTo: log)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: Data(line.utf8))
        } catch {
            NSLog("PrepPal could not append to %@: %@", log.path, "\(error)")
        }
    }

    private func restartServer() {
        status.showLoading("Restarting the server…")
        stopServer { [weak self] _ in self?.startServer() }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard server != nil else { return .terminateNow }
        status.showLoading("Stopping the server…")
        stopServer { _ in sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    // MARK: Menus

    private func buildMenu() {
        let main = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About \(Self.displayName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",").target = self
        appMenu.addItem(withTitle: "Import Existing Data…", action: #selector(importExistingData), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "Copy MCP Command", action: #selector(copyMCPCommand), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "Open Logs", action: #selector(openLogsMenu), keyEquivalent: "").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(Self.displayName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        main.addItem(submenu: appMenu, title: Self.displayName)

        // Without an Edit menu, Command-C and Command-V do nothing inside a WKWebView.
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        main.addItem(submenu: edit, title: "Edit")

        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reloadDesk), keyEquivalent: "r").target = self
        main.addItem(submenu: view, title: "View")

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        main.addItem(submenu: windowMenu, title: "Window")
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = main
    }

    @objc private func reloadDesk() {
        web.reload()
    }

    @objc private func openLogsMenu() {
        openLogs()
    }

    private func openLogs() {
        if let logURL {
            NSWorkspace.shared.open(logURL)
        } else if let locations {
            NSWorkspace.shared.open(locations.logsDirectory)
        }
    }

    @objc private func openSettings() {
        showSettings(reason: nil)
    }

    private func showSettings(reason: String?) {
        let alert = NSAlert()
        alert.messageText = "OpenRouter settings"
        alert.informativeText = (reason.map { $0 + "\n\n" } ?? "") + "Use a free model (its name ends in :free). Saving restarts the server."
        let key = NSSecureTextField(frame: NSRect(x: 0, y: 30, width: 360, height: 24))
        key.placeholderString = "OpenRouter API key"
        let model = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        model.placeholderString = "Model, for example qwen/qwen3-coder:free"
        do {
            key.stringValue = try secrets.read(SecretsStore.apiKeyAccount) ?? ""
            model.stringValue = try secrets.read(SecretsStore.modelAccount) ?? ""
        } catch {
            alert.informativeText += "\n\nCurrent values could not be read: \(error)"
        }
        let fields = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: 54))
        fields.addSubview(key)
        fields.addSubview(model)
        alert.accessoryView = fields
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do {
            try saveOrClear(key.stringValue, SecretsStore.apiKeyAccount)
            try saveOrClear(model.stringValue, SecretsStore.modelAccount)
        } catch {
            presentAlert("The settings were not saved.", "\(error)")
            return
        }
        if locations != nil { restartServer() }
    }

    private func saveOrClear(_ value: String, _ account: String) throws {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            try secrets.delete(account)
        } else {
            try secrets.write(trimmed, for: account)
        }
    }

    @objc private func copyMCPCommand() {
        guard let locations else { return }
        do {
            let json = try MCPCommand.configJSON(appBundle: Bundle.main.bundleURL, locations: locations)
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(json, forType: .string)
            presentAlert("MCP command copied.", "Paste it into your Claude Code MCP configuration.")
        } catch MCPCommandError.translocated {
            presentAlert("Move \(Self.displayName) to Applications first.",
                         "macOS is running the app from a temporary copy, so its path would change after a restart.")
        } catch {
            presentAlert("The MCP command could not be built.", "\(error)")
        }
    }

    @objc private func importExistingData() {
        guard let locations else { return }
        let panel = NSOpenPanel()
        panel.message = "Choose an existing command_center folder to copy into \(Self.displayName)."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let source = panel.url else { return }

        let confirm = NSAlert()
        confirm.messageText = "Import \(source.lastPathComponent)?"
        confirm.informativeText = "The server stops while the folder is copied. The app's current data is moved to a backup folder, not deleted."
        confirm.addButton(withTitle: "Import")
        confirm.addButton(withTitle: "Cancel")
        guard confirm.runModal() == .alertFirstButtonReturn else { return }

        status.showLoading("Importing data…")
        stopServer { [weak self] _ in
            guard let self else { return }
            do {
                let result = try DataImporter().importCommandCenter(from: source, to: locations.commandCenterDirectory)
                let backupNote = result.backup.map { "The previous app data is in \($0.path)." } ?? "There was no previous app data."
                self.presentAlert("Data imported.", backupNote)
            } catch {
                self.presentAlert("Nothing was imported.", "\(error)")
            }
            self.startServer()
        }
    }

    private func presentAlert(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.runModal()
    }
}

private extension NSMenu {
    func addItem(submenu: NSMenu, title: String) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.submenu = submenu
        addItem(item)
    }
}
```

- [ ] **Step 6: Create `Info.plist`**

Create `macos/Resources/Info.plist`. The build script replaces `__VERSION__`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleName</key>
    <string>Andrew's PrepPal</string>
    <key>CFBundleDisplayName</key>
    <string>Andrew's PrepPal</string>
    <key>CFBundleExecutable</key>
    <string>PrepPal</string>
    <key>CFBundleIdentifier</key>
    <string>io.github.jacobtdang.preppal</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>__VERSION__</string>
    <key>CFBundleVersion</key>
    <string>__VERSION__</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
```

- [ ] **Step 7: Add the `app` stage to the build script**

In `macos/build_app.sh`, add this line directly below `SITE=...`:

```bash
VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -1)"
```

Add this function after `stage_python`:

```bash
stage_app() {
  [ -x "$RESOURCES/python/bin/python3" ] || fail "run 'macos/build_app.sh --stage python' first"
  [ -n "$VERSION" ] || fail "could not read the version from pyproject.toml"
  (cd "$ROOT/macos" && swift build -c release --product PrepPal)
  local bin
  bin="$(cd "$ROOT/macos" && swift build -c release --show-bin-path)/PrepPal"
  mkdir -p "$APP/Contents/MacOS"
  cp "$bin" "$APP/Contents/MacOS/PrepPal"
  sed "s/__VERSION__/$VERSION/g" "$ROOT/macos/Resources/Info.plist" >"$APP/Contents/Info.plist"
  plutil -lint "$APP/Contents/Info.plist" >/dev/null || fail "the generated Info.plist is invalid"
}
```

Replace the usage line and the `case` block at the bottom with:

```bash
stage="${2:-}"
[ "${1:-}" = "--stage" ] || fail "usage: macos/build_app.sh --stage python|app"
case "$stage" in
  python) stage_python ;;
  app)    stage_app ;;
  *) fail "unknown stage '$stage'; expected: python or app" ;;
esac
echo "build_app: stage '$stage' done -> $APP"
```

- [ ] **Step 8: Build the app and check the core tests still pass**

Run: `cd macos && swift build -c release --product PrepPal && swift test`

Expected: the build succeeds, and the tests report `Executed 39 tests, with 0 failures`.

Then, from the repo root, run: `macos/build_app.sh --stage python && macos/build_app.sh --stage app`

Expected: `build_app: stage 'app' done -> .../dist/Andrew's PrepPal.app`.

- [ ] **Step 9: Manual check: launch into a throwaway home**

Run:

```bash
export PREPPAL_HOME="$(mktemp -d)"
open --env "PREPPAL_HOME=$PREPPAL_HOME" "dist/Andrew's PrepPal.app"
```

Expected:
- A window titled `Andrew's PrepPal` shows "Starting the server…" and then the desk.
- The header's `LOCAL · PORT` number matches the `READY` port in `$PREPPAL_HOME/Logs/PrepPal/server-*.log`.

- [ ] **Step 10: Manual check: the four browser features**

In the running app:
- **Upload:** add a PDF through the notebook's upload button. The native file picker opens, and the file appears.
- **Confirmation dialog:** close a canvas card. A native OK / Cancel dialog appears, and Cancel keeps the card.
- **New-tab link:** click the link that opens a new tab. It opens in your default browser, not in the app.
- **`localStorage`:** move a card, quit the app, and open it again with the same `open --env` command. The card is where you left it.

- [ ] **Step 11: Manual check: menus**

- **Settings:** save a free model name. The server restarts, and the log shows a new launch.
- **Copy MCP Command:** paste the result into a scratch file. It names `.../Andrew's PrepPal.app/Contents/Resources/python/bin/python3` and the `$PREPPAL_HOME` data folder.
- **Open Logs:** the current `server-*.log` opens.
- **Edit shortcuts:** Command-C and Command-V work in a desk text field.

- [ ] **Step 12: Manual check: quit leaves nothing running**

Quit with Command-Q, then run `pgrep -fl "Andrew's PrepPal.app/Contents/Resources/python"`.

Expected: no output.

- [ ] **Step 13: Manual check: a locked data folder shows the error screen**

Hold the app's data folder with the repo server, then open the app:

```bash
CIRCUIT_MCP_DATA_DIR="$PREPPAL_HOME/Application Support/PrepPal/command_center" .venv/bin/python run_ui.py &
sleep 5
open --env "PREPPAL_HOME=$PREPPAL_HOME" "dist/Andrew's PrepPal.app"
```

Expected:
- The error screen reads "Another Andrew's PrepPal server is using this data folder (process <pid>)."
- It shows the log tail, with Open Log, Restart, and Quit buttons.

Stop the repo server (`kill %1`), press Restart, and the desk loads. Quit the app.

- [ ] **Step 14: Commit**

```bash
git add macos/Package.swift macos/Sources/PrepPal macos/Resources/Info.plist macos/build_app.sh
git commit -m "Add the Andrew's PrepPal app: window, status screens, menus, and bundle assembly."
```

---

### Task 9: Sign, disk image, smoke test, and README

**Files:**
- Modify: `macos/build_app.sh` (add the `sign`, `dmg`, and `all` stages)
- Create: `macos/smoke_test.sh`
- Modify: `README.md` (new section after `## MATLAB bridge (opt-in)`)
- Modify: `docs/superpowers/specs/2026-09-15-macos-app-shell-design.md` (drop the icon from the bundle layout)

**Interfaces:**
- Consumes:
  - the `python` and `app` stages (Tasks 4 and 8)
  - `PREPPAL_HOME` (Task 5)
  - bundle id `io.github.jacobtdang.preppal`
- Produces:
  - `macos/build_app.sh` with no arguments runs every stage and writes `dist/Andrew's PrepPal.app` and `dist/PrepPal-<version>.dmg`.
  - `macos/smoke_test.sh [app path]` exits 0 only after a full launch, health check, quit, and process check.

- [ ] **Step 1: Write the smoke test first**

Create `macos/smoke_test.sh` and make it executable (`chmod +x macos/smoke_test.sh`):

```bash
#!/usr/bin/env bash
# End to end: open the built app in a throwaway home, wait for a healthy server,
# quit the app, and confirm nothing from the bundle is left running.
# Usage: macos/smoke_test.sh [path/to/Andrew's PrepPal.app]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/dist/Andrew's PrepPal.app}"
fail() { echo "smoke_test: $*" >&2; exit 1; }

[ -x "$APP/Contents/MacOS/PrepPal" ] || fail "no built app at $APP; run macos/build_app.sh"
codesign --verify --deep --strict "$APP" 2>/dev/null || fail "the app is not signed; run macos/build_app.sh"
if pgrep -f "$APP/Contents/" >/dev/null; then fail "the app is already running; quit it first"; fi

home="$(mktemp -d)"
logs="$home/Logs/PrepPal"
open --env "PREPPAL_HOME=$home" "$APP"

port=""
for _ in $(seq 1 120); do
  log="$(ls -t "$logs"/server-*.log 2>/dev/null | head -1 || true)"
  if [ -n "$log" ]; then
    line="$(grep -m1 -E '^(READY|LOCKED) ' "$log" || true)"
    case "$line" in
      READY\ *) port="${line#READY }"; break ;;
      LOCKED\ *) cat "$log" >&2; fail "server reported: $line" ;;
    esac
  fi
  sleep 0.5
done
[ -n "$port" ] || fail "no READY line within 60 s (logs in $logs)"

healthy=""
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$port/api/status" 2>/dev/null | grep -q '"ok":true'; then healthy=yes; break; fi
  sleep 0.5
done
[ -n "$healthy" ] || fail "/api/status on port $port never reported ok"

osascript -e 'tell application id "io.github.jacobtdang.preppal" to quit'
for _ in $(seq 1 40); do
  pgrep -f "$APP/Contents/" >/dev/null || break
  sleep 0.5
done
if pgrep -fl "$APP/Contents/"; then fail "processes from the app are still running 20 s after quit"; fi

rm -rf "$home"
echo "smoke_test: the app served port $port, quit cleanly, and left nothing running"
```

- [ ] **Step 2: Run the smoke test to verify it fails**

Run: `macos/smoke_test.sh`

Expected: `smoke_test: the app is not signed; run macos/build_app.sh`, exit 1. The Task 8 bundle exists but is unsigned.

- [ ] **Step 3: Add the sign, disk-image, and all-in-one stages**

In `macos/build_app.sh`, add these functions after `stage_app`:

```bash
stage_sign() {
  [ -x "$APP/Contents/MacOS/PrepPal" ] || fail "run 'macos/build_app.sh --stage app' first"
  # Ad-hoc signature. Notarization with a Developer ID replaces exactly this step.
  codesign --force --deep --sign - "$APP"
  codesign --verify --deep --strict "$APP" || fail "codesign could not verify the signature"
}

stage_dmg() {
  codesign --verify --deep --strict "$APP" >/dev/null 2>&1 || fail "run 'macos/build_app.sh --stage sign' first"
  local dmg="$ROOT/dist/PrepPal-$VERSION.dmg"
  local staging="$WORK/dmg"
  rm -rf "$staging" "$dmg"
  mkdir -p "$staging"
  ditto "$APP" "$staging/$APP_NAME.app"
  ln -s /Applications "$staging/Applications"
  hdiutil create -volname "$APP_NAME" -srcfolder "$staging" -ov -format UDZO "$dmg" >/dev/null
  hdiutil verify "$dmg" >/dev/null || fail "hdiutil could not verify $dmg"
  echo "build_app: disk image -> $dmg"
}
```

Replace the stage selection at the bottom of the file, everything from `stage="${2:-}"` to the final `echo`, with:

```bash
if [ "$#" -eq 0 ]; then
  stage=all
elif [ "${1:-}" = "--stage" ] && [ -n "${2:-}" ]; then
  stage="$2"
else
  fail "usage: macos/build_app.sh [--stage python|app|sign|dmg|all]"
fi

case "$stage" in
  python) stage_python ;;
  app)    stage_app ;;
  sign)   stage_sign ;;
  dmg)    stage_dmg ;;
  all)    stage_python; stage_app; stage_sign; stage_dmg ;;
  *) fail "unknown stage '$stage'; expected python, app, sign, dmg, or all" ;;
esac
echo "build_app: stage '$stage' done -> $APP"
```

Also update the usage comment at the top of the file to `# Usage: macos/build_app.sh [--stage python|app|sign|dmg|all]`.

- [ ] **Step 4: Build everything**

Run: `macos/build_app.sh`

Expected:
- the last lines are `build_app: disk image -> .../dist/PrepPal-0.1.0.dmg` and `build_app: stage 'all' done -> ...`
- `macos/check_python_runtime.sh` still passes on the signed bundle

- [ ] **Step 5: Run the smoke test to verify it passes**

Run: `macos/smoke_test.sh`

Expected: `smoke_test: the app served port <port>, quit cleanly, and left nothing running`.

- [ ] **Step 6: Document the app in the README**

In `README.md`, insert this section directly before `## Development`:

````markdown
## macOS app: Andrew's PrepPal

`Andrew's PrepPal.app` runs the command center in its own window. There's no
browser tab and no `run_ui.py`. The app starts the server itself on a free local
port, shows the desk once the server answers, and stops the server when you quit.

Build it on an Apple silicon Mac with uv installed:

```console
uv python install 3.12
macos/build_app.sh
macos/smoke_test.sh
```

That produces `dist/Andrew's PrepPal.app` and `dist/PrepPal-<version>.dmg`.

- **Data:** `~/Library/Application Support/PrepPal/`. To bring over a
  checkout's `.local/command_center`, use **Import Existing Data…**. Stop
  `run_ui.py` first. The app's current data is moved to a backup folder, not
  deleted.
- **Logs:** `~/Library/Logs/PrepPal/`, with the last five launches kept. If the
  server can't start, the app shows the end of the log.
- **OpenRouter key and model:** stored in the macOS Keychain. Set them with
  **Settings…**, and use a free model.
- **Claude Code:** move the app to Applications, choose **Copy MCP Command**,
  and paste the result into your MCP configuration.
- **Sharing it:** the app is ad-hoc signed, not notarized. On another Mac, open
  it the first time with right-click, **Open**, then **Open** again.
- **Requirements:** Apple silicon and macOS 14 or later.

This release bundles the core tools. Circuit simulation, Showman visuals, iPad
capture, and handwriting OCR are added in later releases. Until then, each one
reports itself unavailable, as it does in a checkout without that runtime.
````

- [ ] **Step 7: Drop the icon from the spec's bundle layout**

This plan doesn't design an app icon, so the layout should not promise one. In `docs/superpowers/specs/2026-09-15-macos-app-shell-design.md`, delete the line `      AppIcon.icns` from the "Bundle layout" block.

- [ ] **Step 8: Run every test and check once more**

Run each command, and confirm its expected result before running the next:

```bash
.venv/bin/python -m pytest -q          # every Python test passes
(cd macos && swift test)               # Executed 39 tests, with 0 failures
macos/check_python_runtime.sh          # bundled Python served port ... and stopped cleanly
macos/smoke_test.sh                    # the app served port ..., quit cleanly, and left nothing running
du -sh "dist/Andrew's PrepPal.app" dist/PrepPal-*.dmg   # app roughly 400 MB
```

- [ ] **Step 9: Commit**

```bash
git add macos/build_app.sh macos/smoke_test.sh README.md docs/superpowers/specs/2026-09-15-macos-app-shell-design.md
git commit -m "Sign and package Andrew's PrepPal, and add an end-to-end smoke test."
```

---

## Acceptance check (manual, after Task 9)

1. Copy `dist/PrepPal-<version>.dmg` to a second macOS user account, or to another Apple silicon Mac, and open it.
2. Drag the app to Applications, then open it with right-click, **Open**. The desk loads.
3. Upload a PDF, and quit and reopen: the upload and card positions are still there.
4. Choose **Copy MCP Command**, add the result to Claude Code, and call `course_progress`. It answers from the app's data folder.

## Not in this plan

- ngspice, Showman, iPad capture, and the OCR installer. These are sub-projects 2–5, each with its own spec and plan.
- An app icon, notarization, and automatic updates.
- Intel Macs.
