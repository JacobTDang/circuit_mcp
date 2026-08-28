# Verification matrix

This is an acceptance record, not a claim that arbitrary EE 2300 work is
infallibly understood. Run all checks with:

```console
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Last full native run: 2026-08-27 on Apple Silicon, Python 3.12, ngspice 47:
319 passed. Three subsequent fresh-server course/transport repetitions passed;
concurrency, forced-timeout recovery, state isolation, and `git diff --check`
also passed. UniMERNet loaded on MPS in 6.98 seconds. No physical VISA
instrument was attached, so its real electrical readings remain
hardware-dependent; its disabled-by-default policy, SCPI allow-list, resource
lifecycle, timeouts, and fake-instrument E2E are tested.

iPad capture acceptance on 2026-08-27: UxPlay 1.74 built natively on Apple
Silicon with GStreamer 1.28.6; 10 receiver start/idempotent-start/stop cycles
used 10 distinct clean PIDs; 100 concurrent source-status requests completed
in 0.03 seconds after USB discovery caching; headless H.264-to-PNG capture,
shared live-frame decoding, transient no-store HTTP delivery,
AirPlay-to-USB fallback, invalid frames, persistence, MCP transport discovery,
and browser controls are tested. The connected iPad was not exposed as a
CoreMediaIO muxed USB device during verification, so a real USB frame remains
device/trust/permission-dependent.

VISA backend audit on 2026-08-26: PyVISA-py 0.8.1, PySerial 3.5,
zeroconf 0.150.0, PyUSB 1.3.1, and Homebrew libusb 1.0.30 are operational.
Serial, USBTMC, VXI-11, HiSLIP, and TCPIP discovery paths load successfully.
No physical USB or network instrument was attached; only the Mac debug,
Bluetooth incoming, and Bose serial endpoints were found and none was queried.

`tests/test_course_questions.py` launches the checked-in stdio command,
negotiates MCP, submits a mixed assignment, and checks independently stated
answers. It covers numeric/symbolic dividers; RC and RL low/high-pass networks;
first- and second-order transfer functions; ideal, finite-gain, and finite-GBW
op-amps; operating point; DC sweep; AC response; transient response; and a
nonlinear diode. `tests/test_spice.py` independently checks the numeric values
and the ngspice security boundary.

| EE 2300 family | Verification level |
|---|---|
| KCL/KVL, node voltages, branch currents | Exact symbolic solution and setup/rank checks |
| Algebraic derivations | Symbolic proof plus numeric counterexample oracle |
| RC/RL/RLC transfer functions and poles | Exact symbolic, live MCP acceptance cases |
| Ideal/finite/one-pole-GBW op-amps | Exact symbolic, live MCP acceptance cases |
| Operating point and DC sweep | Numeric ngspice, independent expected values |
| Sinusoidal/AC response | Complex ngspice values checked at known RC behavior |
| Time-domain response | RC step checked against `1-exp(-t/RC)` |
| Nonlinear devices | Diode bias checked against KVL and physical voltage range |
| ADC/DAC circuits | Generic symbolic/SPICE plus quantization/code tables, endpoint INL/DNL, missing codes, and monotonicity |
| Stability/feedback margins | Numeric poles/zeros, stability, bandwidth, gain/phase margins, crossovers, step metrics |
| Spectra/distortion | Coherent FFT harmonic amplitude, THD, SINAD, and ENOB grader |
| Diode/BJT/MOS DC and small signal | ngspice operating point and bias-linearized AC, checked against device equations |
| Comparators/rectifiers/oscillators | Rail-limited comparator, half-wave rectifier, and startup/transitions of relaxation oscillator |
| Mixed signal | Native XSPICE ADC-to-DAC bridge transient fixture |
| Op-amp static/dynamic limits | GBW/noise-gain bandwidth, slew demand, full-power bandwidth, saturation through SPICE models |
| Lab measurements | Bounded CSV import; opt-in read-only PyVISA discovery/query; no write commands |
| Advanced numeric analysis | ngspice transfer/port impedance, pole-zero, sensitivity, Johnson noise, and nonlinear distortion |
| Automatic handwritten circuit topology | Not implemented; formula OCR requires confirmation |

Safety tests cover parser rejection, named errors, process timeouts and restart,
per-request process isolation, concurrent calls, invalid capture regions,
strict PNG/base64 handling, OCR-worker restart/timeout, prohibited SPICE file
and control directives, malformed analyses, point limits, and unknown vectors.

## Visual and agent acceptance run (2026-08-27)

Twenty-four original, illustrated EE 2300 worksheets were generated and uploaded
through the live localhost:2300 multipart library endpoint. Fresh Codex agents
read each PNG, stopped for transcription confirmation, and then called the
registered MCP tools. The final result was 24/24 correct. The visual audit
caught and corrected an ambiguous op-amp drawing, an under-specified BJT
question, and an incorrect expected damping label before the corpus was kept.

A separate 20-case adversarial agent run covered correct and incorrect
equivalence, KCL, derivation errors, stable/unstable/marginal poles, feedback,
ADC boundaries, op-amp equality limits, safe SPICE rejection, nonlinear bias,
and spectral distortion. It initially returned 16 PASS and four semantic GAPs.
The gaps were fixed (parameterized derivations, explicit stability classes,
separate negative-unity closed-loop results, and explicit ADC monotonicity
semantics), then a fresh black-box agent regression passed all 5 fix checks.

The seeded `scripts/stress_ee230.py` matrix ran twelve seeds for 11,950/11,950
passes across algebraic equivalence, parameterized derivations, three stability
classes, ADC transition behavior, op-amp limits, rectifiers, BJT followers,
oscillators, DACs, aliasing, transimpedance stages, and 250 varied ngspice
dividers. This run exposed binary-float phantom derivation failures; parameter
values are now converted from their JSON decimal spelling to exact rationals,
and a dedicated regression covers the failing value.

All 24 uploaded visual problems were downloaded again and SHA-256 compared to
their source PNGs with zero mismatches. The six tools added after the second
visual run passed 6/6 through a fresh black-box Codex client, 12/12 adversarial
validity checks, and 120/120 concurrent localhost calls. UniMERNet loaded and
inferred on MPS,
but intentionally failed to produce useful output from a full worksheet page;
the API and UI now explicitly scope it to one tightly cropped mathematical
expression and direct full-page/circuit interpretation to agent vision.

## SQLite command-center acceptance (2026-08-27)

The live JSON index/history migrated idempotently to SQLite with 24 documents,
383 events, verified file hashes, clean foreign keys, and recoverable legacy
backups. A real localhost workflow linked an uploaded RC worksheet, confirmed
problem interpretation, student/agent attempt, symbolic MCP evidence, and a
correct completion. The progress API then reported one confirmed RC-filter
problem and one correct attempt.

The database suite covers invalid migration rollback, FTS5, correction
revisions, soft deletion/trash, tags, bounded JSON evidence, injection strings,
40 concurrent repository writers, missing/tampered/orphan file detection, and
online backup/restore. The real stdio MCP transport exercises problem and
attempt writes against an isolated database. A live 20-way HTTP run completed
200/200 tool calls and produced exactly 200 evidence rows plus 200 event rows;
post-run database, foreign-key, and 24-file integrity checks remained clean.
