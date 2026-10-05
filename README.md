# circuit_mcp

![circuit_mcp: a sign error found at the step it happened, then the schematic, breadboard build and expected scope readings for the same circuit](docs/media/circuit-mcp.gif)

An MCP server that checks circuit homework without doing it for you. Give it
your working and it finds the first step that is wrong. The language model only
reads your work. lcapy, SymPy and ngspice decide whether it is right.

## What it does

- **Finds the first mistake.** `check_setup` tests your circuit equations,
  `check_derivation` tests each algebra step, and the answer is compared with the
  transfer function `derive` works out from the netlist.
- **Reads your iPad.** Mirror the iPad and the agent captures the page. Local
  handwriting OCR turns each line into LaTeX. The agent shows what it read and
  waits for you to confirm before it checks anything.
- **Explains on a desk.** A local web page, or the macOS app, shows cards beside
  your work: formulas, algebra walkthroughs, and a schematic drawn from the same
  netlist that was checked. Every card is verified before it is drawn.
- **Gets you to the bench.** A breadboard card gives the build in wiring order.
  An expected card says what the meter and scope should read. `compare_readings`
  names the likely cause of a reading that is off.
- **Makes a page to hand in.** `/solutions?tag=<tag>` prints a whole assignment,
  each answer backed by the checks that were recorded for it.

Course files stay on your machine.

## Quick start

You need macOS or Linux, Python 3.12+, and ngspice 47+ for simulation
(`brew install ngspice`). Windows is not supported.

```console
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q      # tests
.venv/bin/python run_server.py     # the MCP server, over stdio
.venv/bin/python run_ui.py         # the desk, at http://localhost:2300
```

The checked-in [`.mcp.json`](.mcp.json) registers the server with Claude Code.
That project registration deliberately uses this checkout's `.local/command_center` data. The
macOS app's **Register with Claude Code…** command instead creates a user-scope registration that
uses `~/Library/Application Support/CircuitMCP/command_center`, the same data shown on the app's
desk. Inside this checkout the project registration takes precedence; `workspace_status` reports
the active `data_dir` and whether it is checkout or app data.
[`CLAUDE.md`](CLAUDE.md) is the tutoring workflow the agent follows.

### Optional add-ons

Each one reports itself as unavailable, and says what is missing, until it is
set up.

| Add-on | Set up | More |
|---|---|---|
| iPad capture | `scripts/setup_ipad_capture.sh` | [ipad-capture.md](docs/ipad-capture.md) |
| Handwriting OCR (about 2 GB) | `scripts/setup_ocr.sh`, or install from the iPad page | [reference](docs/reference.md#handwriting-recognition) |
| Teaching videos | the `vendor/showman` submodule, Node, and an OpenRouter key in `.env` | [reference](docs/reference.md#teaching-videos) |
| MATLAB | `CIRCUIT_MCP_ENABLE_MATLAB=1` in the shell only, never in `.mcp.json` | [reference](docs/reference.md#matlab-bridge) |
| Lab instruments | `CIRCUIT_MCP_ENABLE_INSTRUMENTS=1`, read-only VISA; the checked-in `.mcp.json` sets it | [reference](docs/reference.md#configuration) |

## Checking work on an iPad

1. Run `scripts/setup_ipad_capture.sh` once.
2. Start the receiver on the desk's iPad page, or ask the agent to.
3. On the iPad: Control Center → **Screen Mirroring** → **Circuit Capture**, then
   enter the four-digit PIN.
4. Ask the agent to check your solution. It captures the screen, falling back to
   a USB-C iPad when AirPlay is off.
5. Confirm or correct its transcription. Only then does it check the maths.

Nothing is recorded in the background. A frame is saved only when you, or the
agent, snap one.

## macOS app

`Circuit MCP.app` runs the desk in its own window and starts and stops the
server for you. It carries its own ngspice, and the AirPlay receiver too when
the build machine has built one. Build it on an Apple silicon Mac:

```console
uv python install 3.12
brew install ngspice
macos/build_app.sh
macos/smoke_test.sh
```

That makes `dist/Circuit MCP.app` and a `.dmg`. In the app, **Copy MCP Command**
gives you the line to paste into Claude Code. The app is not notarized, so
another Mac needs one extra step the first time; see
[opening it on another Mac](docs/reference.md#opening-it-on-another-mac).

## Open it in linkC

[linkC](https://github.com/JacobTDang/linkC) can open the desk in a tab of this
project, from [`.linkc/app.json`](.linkc/app.json). Closing the tab stops the
server. Only one server can use the data folder at a time, so the tab says so
if the app is already running.
[More](docs/reference.md#open-it-in-linkc).

## Tools

The tools cover checking and deriving, simulation and measurement, iPad
capture and OCR, the course record, desk cards, and teaching videos.

<details>
<summary>All tools</summary>

| Tool | Purpose |
|---|---|
| `derive` | Derive a transfer function and poles in finite, ideal, or finite-GBW mode; a voltage or a current input |
| `port_impedance` | Impedance looking into a port, with the independent sources killed; input and output resistance, derived rather than simulated |
| `check_equivalence` | Compare two expressions and return a counterexample when they differ |
| `check_derivation` | Locate the first invalid algebra transition, setup error, or wrong final answer; optional parameters support symbolic-to-numeric steps |
| `circuit_equations` | Return lcapy's nodal system and solved circuit quantities |
| `check_setup` | Check that submitted equations hold and have full rank; classify each equation's role |
| `compare_readings` | Check measured bench readings against a build's prediction and name the likeliest cause of each miss |
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
| `transcribe_page` | Convert each handwritten expression on one base64 PNG page to local LaTeX, with its box |
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
| `dac_output` | Map codes of an ideal straight-binary span DAC to voltages; for a resistor summing-amplifier DAC use `summing_dac_output` |
| `summing_dac_output` | Predict an inverting op-amp summing DAC's outputs from its actual resistors, with each code's error against ideal |
| `alias_frequency` | Fold a sinusoid into the first Nyquist zone |
| `transimpedance` | Compute ideal current-input inverting op-amp output |
| `import_waveform_csv` | Parse bounded oscilloscope/DMM CSV payloads without filesystem access |
| `instrument_status` | Discover VISA instruments only after explicit opt-in |
| `instrument_query` | Send allow-listed read-only SCPI queries; write commands are prohibited |
| `matlab_status` | Report whether MATLAB is enabled and whether a session is warm, without starting it |
| `matlab_eval` | Evaluate MATLAB code in a persistent Engine session; return text and an optional figure PNG |
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
| `canvas_card_add` | Put a verified card on the desk (formula, walkthrough, vocabulary, schematic, breadboard, expected), or a problem's solution on its solutions sheet |
| `canvas_card_list` | List the cards on the desk, newest first, optionally for one problem |
| `canvas_card_remove` | Take one card off the desk |
| `visual_status` | Report whether the local renderer can author a visual |
| `visual_generate` | Render one local teaching video from a brief and persist it |
| `visual_list` | List locally rendered visuals, newest first |
| `visual_get` | Read one visual and the specification it was rendered from |
| `visual_preview` | Return one still frame of a stored visual as an image |

</details>

`derive`, `check_equivalence`, `check_derivation`, `check_setup` and
`simulate_spice` take an optional `attempt_id`. Pass one and the verdict is
recorded against that attempt, for `attempt_history` and the problem board.

## How it stays honest

- **The model reads; the maths decides.** OCR and vision only transcribe. Every
  verdict comes from lcapy and SymPy, or from ngspice for numeric results.
- **You confirm first.** The agent echoes every transcribed equation and the
  circuit it thinks it sees, and waits. A misread sign or subscript would
  otherwise produce a confident wrong verdict.
- **Cards can't disagree with the maths.** A walkthrough is refused unless every
  step is an identity. A schematic is read back from its own drawing and refused
  unless it matches the netlist.
- **Input is screened.** Expressions use a small, safe SymPy syntax, and
  simulation decks can't include files or shell commands. See
  [expressions and results](docs/reference.md#expressions-and-results).

## Limits

- Symbolic analysis covers linear circuits, including op amps with finite
  gain, ideal gain, or a single-pole gain-bandwidth model.
- Nonlinear and time-domain work goes through `simulate_spice`. That is numeric
  evidence, not an algebraic proof.
- It can't infer circuit connectivity from handwriting. You confirm the netlist.
- Windows is not supported: each call runs in a forked child that can be killed
  on a timeout, and Windows has no `fork`.

## Docs

- [Reference](docs/reference.md): setup, configuration and behaviour in full
- [iPad capture](docs/ipad-capture.md): the AirPlay and USB-C backends
- [Design](docs/2026-08-24-design.md): rationale, alternatives and lcapy limits
- [Storage](docs/storage.md): the SQLite database, migrations and recovery
- [Verification](docs/verification.md): what has been tested, and how
