# circuit_mcp

An MCP server that checks circuit derivations against a symbolic ground truth
for Iowa State EE 2300 (Electronic Circuits and Systems).

It does not solve homework on the student's behalf. Given a netlist and an
ordered derivation, it identifies where the work diverges: setup equations are
checked against the circuit's solved values, algebra is checked step-to-step,
and the final expression is checked against the ground-truth transfer function.
The language model handles transcription; lcapy and SymPy make the mathematical
verdicts.

## Status

The linear-circuit MCP server is implemented and registered by [`.mcp.json`](.mcp.json).
It currently provides forty-three tools:

| Tool | Purpose |
|---|---|
| `derive` | Derive a transfer function and poles in finite, ideal, or finite-GBW mode |
| `check_equivalence` | Compare two expressions and return a counterexample when they differ |
| `check_derivation` | Locate the first invalid algebra transition, setup error, or wrong final answer; optional parameters support symbolic-to-numeric steps |
| `circuit_equations` | Return lcapy's nodal system and solved circuit quantities |
| `check_setup` | Check that submitted equations hold and have full rank; classify each equation's role |
| `workspace_status` | Check whether the macOS screenshot backend is available without capturing anything |
| `capture_workspace` | Return the current visible iPad screen or selected region as an MCP PNG image |
| `ipad_capture_status` | Report managed AirPlay and USB-C source health |
| `ipad_receiver_start` | Start the PIN-protected local UxPlay receiver |
| `ipad_receiver_stop` | Stop the AirPlay receiver and discard its ephemeral PIN |
| `capture_ipad_screen` | Capture iPadOS from AirPlay, automatically falling back to USB-C |
| `workspace_configuration` | Return the saved privacy-scoped iPad screen source |
| `configure_workspace` | Save an iPad screen rectangle without enabling unrelated full-display capture |
| `ocr_status` | Report or warm the persistent UniMERNet worker and selected device |
| `transcribe_image` | Convert one base64 PNG formula crop to local LaTeX |
| `transcribe_workspace` | Capture the configured region and return its image plus local LaTeX |
| `simulate_spice` | Run a bounded local ngspice operating-point, DC-sweep, AC, or transient analysis |
| `characterize_transfer` | Compute poles, zeros, explicit stability class, margins, bandwidth, step metrics, and optional unity-feedback closed-loop results |
| `converter_metrics` | Grade ADC/DAC INL, DNL, missing codes, and explicit nondecreasing/strict monotonicity |
| `spectrum_metrics` | Compute coherent harmonics, THD, SINAD, and ENOB |
| `quantize` | Produce ideal ADC codes, bin-center reconstruction, clipping, and quantization error |
| `opamp_limits` | Check closed-loop bandwidth, full-power bandwidth, and slew rate |
| `rectifier_metrics` | Analyze constant-drop half-wave conduction and DC average |
| `bjt_emitter_follower` | Compute hybrid-pi gm, r-pi, and loaded voltage gain |
| `relaxation_oscillator` | Compute symmetric Schmitt-RC period and frequency |
| `dac_output` | Map ideal straight-binary DAC codes to voltages |
| `alias_frequency` | Fold a sinusoid into the first Nyquist zone |
| `transimpedance` | Compute ideal current-input inverting op-amp output |
| `import_waveform_csv` | Parse bounded oscilloscope/DMM CSV payloads without filesystem access |
| `instrument_status` | Discover VISA instruments only after explicit opt-in |
| `instrument_query` | Send allow-listed read-only SCPI queries; write commands are prohibited |
| `library_search` | Search local document names and indexed study text |
| `document_get` | Read document metadata and bounded extracted text by opaque ID |
| `problem_get` | Read one stored problem and its tags |
| `study_context` | Find local notes and confirmed problems relevant to a query |
| `attempt_history` | Read prior attempts and summarized MCP evidence |
| `course_progress` | Summarize problem and attempt states by topic |
| `problem_create` | Create a bounded local problem record |
| `problem_update_interpretation` | Store a user-confirmed circuit interpretation |
| `transcription_confirm` | Confirm OCR or preserve a corrected revision |
| `attempt_create` | Start a student or agent attempt |
| `attempt_complete` | Complete an attempt with a graded workflow status |
| `problem_tag` | Attach a normalized course tag |

The detailed rationale, evaluated alternatives, and known lcapy limitations are
recorded in [`docs/2026-08-24-design.md`](docs/2026-08-24-design.md).
The SQLite ownership, migration, recovery, and context-tool design is in
[`docs/storage.md`](docs/storage.md).

## Requirements and platform support

- Python 3.12 or newer
- lcapy 1.26 or newer
- SymPy 1.14 or newer
- MCP 1.2 or newer
- ngspice 47 or newer for `simulate_spice` (`brew install ngspice` on macOS)
- A POSIX operating system with `fork`, process groups, and file-descriptor
  polling (macOS and Linux)

Windows is not currently supported. Tool calls run in a prewarmed worker that
forks a disposable child for every request. This isolates lcapy's process-global
symbol registry and makes the 20-second wall-clock bound real: the worker's
entire process group can be killed if symbolic evaluation runs away. A Windows
port needs a spawn-based worker with equivalent isolation and timeout semantics;
silently falling back to an unkillable thread would violate those guarantees.

ngspice is optional for the symbolic tools, but required for nonlinear,
time-domain, and numeric frequency-domain simulation.

### Local process architecture

```text
Claude / Codex
      └── circuit_mcp (native macOS, stdio MCP)
            ├── UxPlay + GStreamer (PIN-protected AirPlay receiver)
            ├── CoreMediaIO/AVFoundation helper (USB-C fallback)
            ├── FFmpeg (latest headless AirPlay H.264 frame → PNG)
            ├── /usr/sbin/screencapture (legacy manual crop only)
            ├── UniMERNet worker (spawned once, persistent, PyTorch MPS)
            ├── ngspice (isolated temporary deck, one bounded analysis)
            ├── SQLite (documents, problems, attempts, and evidence)
            └── symbolic worker (prewarmed, forks an isolated child per call)
```

UniMERNet lives in its own `.venv-ocr.nosync` environment. This prevents its
PyTorch/NumPy dependency graph from changing the proven lcapy environment and
keeps Metal initialization out of every process that later calls `fork()`.

## iPad screen-mirroring workflow

1. Run `scripts/setup_ipad_capture.sh` once.
2. Start the receiver in the command center or call `ipad_receiver_start`.
3. On the iPad, open Control Center → **Screen Mirroring** → **EE2300 Capture**,
   then enter the four-digit PIN shown by the tool or UI.
4. Ask the agent to “check my solution.” It calls `capture_ipad_screen`; when
   AirPlay is unavailable, a trusted USB-C iPad is tried automatically.
5. The agent shows its visual transcription for confirmation before grading.

Capture is local and never records in the background. The workspace card shows
transient no-store frames while connected, but a PNG is retained only after an
explicit Snap/tool call. AirPlay uses an ephemeral PIN and runs headlessly; the latest received
H.264 frame is decoded without opening or capturing a Mac window. USB-C uses
Apple's CoreMediaIO/AVFoundation screen-device path.
Every returned frame includes a SHA-256 hash.

## Visual explanations

The former browser SVG renderer has been retired. Showman is pinned under
`vendor/showman` and will provide deterministic, smooth circuit animations,
camera motion, previews, and local video rendering. Existing scene records are
retained for migration but are no longer spawned by the board. See the
[Showman integration scope](docs/SHOWMAN_INTEGRATION.md).

MCP clients decide when to call tools, so “continuous awareness” means the agent
takes a fresh frame when asked or periodically during an active tutoring loop;
the server does not push images into an idle conversation.

The older manual rectangle workflow remains available for unusual setups:

```text
configure_workspace(x=<left>, y=<top>, width=<width>, height=<height>)
```

It is saved in the ignored `.local/workspace.json`. Explicit coordinates on a
later `transcribe_workspace` call override the saved values for that call.

## Development

Create a Python 3.12+ virtual environment and install the project with its test
dependencies:

```console
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run the test suite:

```console
.venv/bin/python -m pytest -q
```

Start the stdio MCP server directly:

```console
.venv/bin/python run_server.py
```

Start the local Circuit Command Center:

```console
.venv/bin/python run_ui.py
```

Open [http://localhost:2300](http://localhost:2300). It binds only to
`127.0.0.1`, stores uploaded homework and lecture material under the ignored
`.local/command_center/` directory, accepts PDF/PNG/JPEG/text/Markdown/CSV up
to 50 MB, and never sends course files to a cloud service. The UI provides
an initially blank spatial desk. Click empty space to manually spawn an iPad
screen, course files, problem board, circuit bench, or activity panel. Panels
can be dragged, closed, and retain their layout in browser-local storage. The
underlying pages still provide search, previews, local formula OCR, attempt
tracking, status, and circuit-analysis forms. Files remain on disk; SQLite stores metadata, confirmed
transcription revisions, problems, attempts, and tool evidence.

Create a verified online database backup with:

```console
PYTHONPATH=src .venv/bin/python scripts/backup_database.py
```

The checked-in `.mcp.json` uses that command. The worker is prewarmed at server
startup so the first interactive call does not pay lcapy's import cost. The
checkout-local launcher adds `src` explicitly; this also avoids the documented
macOS/iCloud case where Python silently ignores a hidden editable-install `.pth`.

### Install local handwriting OCR

The OCR stack is intentionally separate from the main virtual environment:

```console
./scripts/setup_ocr.sh
```

This creates `.venv-ocr.nosync`, installs UniMERNet 0.2.3, and downloads the
`wanderkid/unimernet_small` checkpoint to the ignored `models/` directory.
Together they currently consume about 1.9 GB on disk. Verify native Metal
loading through MCP with `ocr_status(load_model=true)`; `device` should be
`mps` on Apple Silicon.

| Variable | Default |
|---|---|
| `CIRCUIT_MCP_OCR_PYTHON` | `.venv-ocr.nosync/bin/python` |
| `CIRCUIT_MCP_OCR_MODEL` | `models/unimernet_small` |
| `CIRCUIT_MCP_OCR_DEVICE` | `auto` (`mps`, then CUDA, then CPU) |
| `CIRCUIT_MCP_WORKSPACE_CONFIG` | `.local/workspace.json` |
| `CIRCUIT_MCP_ENABLE_INSTRUMENTS` | unset; set exactly `1` to enable read-only VISA access |
| `CIRCUIT_MCP_DATA_DIR` | `.local/command_center` |
| `CIRCUIT_MCP_AIRPLAY_SIZE` | `800x600@30`; headless receiver stream resolution and frame rate |

The model loads lazily and remains resident. If its process crashes, the
supervisor replaces it and retries the request. A 120-second deadline kills a
wedged worker and releases its Metal memory.

### Handwriting benchmark

Add private PNG samples and expected LaTeX entries to
`benchmarks/handwriting/manifest.json`, then run:

```console
.venv/bin/python scripts/benchmark_ocr.py
```

The benchmark uses whitespace-normalized exact LaTeX matching. Keep personal
handwriting images out of git; only the empty manifest is tracked.

## Input and output contract

Expressions use an intentionally small ASCII SymPy syntax. Common notation such
as `s^2`, `2R`, and `0.5` is normalized, while attribute access, unknown function
calls, Python keywords, Unicode lookalikes, and other interpreter escape routes
are rejected before parsing.

Rendered expressions contain two forms:

- `text`: readable and suitable for feeding into another MCP tool call.
- `srepr`: an exact SymPy record that preserves symbol assumptions.

Every response has `ok`. An `error` field means the tool could not run, such as
for a malformed netlist, unsafe expression, or timeout. A `kind` field means the
check ran and describes the mathematical verdict.

`check_setup` also returns `equation_roles`. Each satisfied equation is labelled
`law`, `solved`, `ambiguous`, or `trivial`, with an explanation and any isolated
unknown/value. Classification is advisory: in small circuits a textbook law and
a solved answer may be algebraically identical, so ambiguity is reported rather
than guessed away.

UniMERNet output is also untrusted. It recognizes cropped formulas, not page
layout, circuit connectivity, or whether two drawn wires cross. The response
includes the exact captured PNG alongside LaTeX so the agent can echo both and
wait for confirmation. `CLAUDE.md` and `AGENTS.md` make that confirmation a
required project workflow.

For s-domain circuits, write an explicitly s-domain source such as
`Vs 1 0 s {V}`. With a capacitor, `Vs 1 0 {V}` asks lcapy for the DC steady state
and therefore contains no `sC` term.

## Scope

The server covers symbolic linear circuits, including op-amps with finite
constant gain, the ideal-gain limit, and a single-pole finite gain-bandwidth
model. lcapy rejects s-dependent component values, so GBW mode derives with a
constant gain `A` and then safely substitutes `A0 / (1 + s/wp)`. Symbol binding
and substitution are checked explicitly to prevent assumption mismatches from
producing plausible but incorrect verdicts.

`simulate_spice` extends coverage to numeric operating points, DC sweeps, AC
responses, transients, transfer/impedance, pole-zero, sensitivity, noise, distortion, and
nonlinear or XSPICE mixed-signal device models. Its deck cannot contain
file includes, control/shell blocks, embedded analyses, or `.end`; the server
adds one validated analysis and runs ngspice without user startup files in a
temporary directory. Simulation is numeric evidence, not an algebraic proof.

Current course-coverage boundaries are explicit: the harness can grade
ADC/DAC quantization, code tables, INL/DNL, and spectral metrics and verify a
student-provided converter network. Numeric AC analysis uses ngspice's
operating-point small-signal linearization. Laboratory data can arrive as CSV
or through explicitly enabled, allow-listed, read-only VISA queries. It also
cannot infer circuit connectivity from handwriting; the agent must confirm the
transcription/netlist with the student first. See
[`docs/verification.md`](docs/verification.md) for the tested matrix.
