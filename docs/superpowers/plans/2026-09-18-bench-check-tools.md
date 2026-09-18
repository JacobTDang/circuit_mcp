# Bench-Check Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three MCP tools that catch bench and transcription mistakes while the circuit is still built: `compare_readings` (#51), `summing_dac_output` (#52), `transcribe_page` (#53).

**Architecture:** Each tool is a pure implementation function registered in `server.py`'s `_IMPLEMENTATIONS` and exposed through an `@server.tool()` wrapper, following the existing pattern (`_guarded` for pure computation; `OCR_WORKER.call` for OCR). Errors are module-specific `ValueError` subclasses mapped to named kinds in `_ERROR_KINDS`.

**Tech Stack:** Python 3.12, uv, pytest, numpy, ngspice (via `circuit_mcp.spice`), UniMERNet in the separate OCR worker process.

**Spec:** `docs/superpowers/specs/2026-09-18-bench-check-tools-design.md`

## Global Constraints

- Run tests as `uv run python -m pytest -q` from the repo root. Plain `pytest` cannot import `tests.fixtures`. Baseline on `main`: 686 passed.
- No new dependencies. numpy is declared; PIL is used only inside the OCR worker process (`ocr_worker.py`), never imported by server code.
- Test-driven: write the failing test, watch it fail, implement, watch it pass.
- Every new tool must be added in four places: the implementation in `_IMPLEMENTATIONS` (only for `_guarded` tools), the `@server.tool()` wrapper, `TOOL_NAMES` in `tests/test_server.py`, and the tool-name set in `tests/test_transport.py`. Both test sets use exact equality.
- New error classes subclass `ValueError` and get an entry in `server.py`'s `_ERROR_KINDS` so failures return a named kind, never `internal_error`.
- Stage files by explicit path (`git add path/one path/two`). Never `git add -A` or `git add .`: the working tree has untracked `macos/` and a dirty `vendor/showman` that must stay out of every commit.
- Commit messages are one sentence in plain English ending with a period, matching the repo (e.g. "Remove the breadboard-cards PR demo image."). Do not mention Claude and do not add a Co-Authored-By trailer.
- Each task ends with the FULL suite green, not only the new tests.

## Branches (controller handles branching, PRs and merges)

| Task | Branch | Issue |
|---|---|---|
| 1 | `feat/compare-readings` | #51 |
| 2 | `feat/summing-dac` | #52 |
| 3, 4 | `feat/transcribe-page` | #53 |

---

### Task 1: compare_readings

**Files:**
- Create: `src/circuit_mcp/compare.py`
- Create: `tests/test_compare.py`
- Modify: `src/circuit_mcp/server.py` (import, `_IMPLEMENTATIONS`, `_ERROR_KINDS`, new tool after the `canvas_card_add` tool)
- Modify: `tests/test_server.py` (import tool, `TOOL_NAMES`, two tests)
- Modify: `tests/test_transport.py` (tool-name set)
- Modify: `CLAUDE.md` (one sentence in workflow step 9)

**Interfaces:**
- Consumes: `circuit_mcp.breadboard.parse_build(content) -> Build` (raises `BuildError`); `Build.sources` items have `.ref`, `.kind` ("dc" | "sine" | "square"), `.node`, `.amplitude` (dc: volts, sine: V RMS, square: V pk-pk); `Build.probes` items have `.label`, `.node`, `.role`; `Build.opamps` items have `.out`. `circuit_mcp.expect.expectations(content) -> dict` (raises `ExpectError`) returning `readings` (dc: `{"label","node","role","kind":"dc","volts"}`; ac: `{"label","node","role","kind":"ac","vpp","vpk","vrms","mean","clipped",...}`), `gains` (`{"output","input","gain","basis": "peak"|"dc",...}`), `swing` (`{"low","high"}`).
- Produces: `circuit_mcp.compare.compare_readings(build, measured, tolerance_pct=5.0) -> dict` and `CompareError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compare.py`:

```python
"""Measured readings against the build's prediction, on the Lab 1 circuits.

ngspice runs for real, as in test_expect.py: the prediction side is the same
expectations() the expected card uses.
"""
from __future__ import annotations

import copy

import pytest

from circuit_mcp.compare import CompareError, compare_readings
from circuit_mcp.expect import expectations
from tests.fixtures import lab1


def entry(result, label):
    return next(r for r in result["readings"] if r["label"] == label)


def predicted(build):
    return {r["label"]: r for r in expectations(build)["readings"]}


def test_readings_that_match_the_prediction_pass():
    p = predicted(lab1.EXP1_NONINVERTING)
    result = compare_readings(lab1.EXP1_NONINVERTING,
                              {"CH1 vi": p["CH1 vi"]["vrms"], "CH2 vo": p["CH2 vo"]["vrms"]})
    assert result["ok"] is True
    assert result["tolerance_pct"] == 5.0
    assert result["all_within_tolerance"] is True
    assert result["gains"][0]["within_tolerance"] is True
    assert "hint" not in entry(result, "CH1 vi")


def test_exp1_probe_on_the_generator_is_flagged_as_a_source_reading():
    # The Lab 1 write-up: CH1 read the generator's 1 V RMS, not vi at the op-amp pin.
    result = compare_readings(lab1.EXP1_NONINVERTING, {"CH1 vi": 0.9955, "CH2 vo": 7.99})
    vi = entry(result, "CH1 vi")
    assert vi["within_tolerance"] is False
    assert vi["hint"]["kind"] == "source"
    assert "VS" in vi["hint"]["message"]
    gain = result["gains"][0]
    assert gain["measured"] == pytest.approx(8.03, abs=0.01)
    assert gain["within_tolerance"] is False
    assert result["all_within_tolerance"] is False


def test_dac_output_with_a_dropped_minus_sign_is_a_sign_error():
    vo = entry(compare_readings(lab1.EXP7_DAC, {"vo": 5.0}), "vo")
    assert vo["within_tolerance"] is False
    assert vo["hint"]["kind"] == "sign"


def test_dac_output_at_the_negative_rail_is_clipping():
    vo = entry(compare_readings(lab1.EXP7_DAC, {"vo": -9.45}), "vo")
    assert vo["hint"]["kind"] == "rail"


def test_a_zero_volt_expectation_uses_an_absolute_band():
    build = copy.deepcopy(lab1.EXP7_DAC)
    for source in build["sources"]:
        source["volts"] = 0          # code 000
    near = entry(compare_readings(build, {"vo": 0.02}), "vo")
    assert near["error_pct"] is None
    assert near["within_tolerance"] is True
    assert entry(compare_readings(build, {"vo": 0.2}), "vo")["within_tolerance"] is False


def test_peak_to_peak_readings_are_compared_in_their_own_basis():
    p = predicted(lab1.EXP1_NONINVERTING)
    vo = entry(compare_readings(lab1.EXP1_NONINVERTING, {"CH2 vo": {"vpp": p["CH2 vo"]["vpp"]}}), "CH2 vo")
    assert vo["basis"] == "vpp"
    assert vo["within_tolerance"] is True


def test_an_unknown_probe_label_is_an_error_not_dropped():
    with pytest.raises(CompareError, match="no probe labelled 'CH3'"):
        compare_readings(lab1.EXP1_NONINVERTING, {"CH3": 1.0})


@pytest.mark.parametrize("measured", [
    {},
    {"vo": "5"},
    {"vo": float("nan")},
    {"vo": True},
    {"vo": {"vrms": 1.0}},              # vo is a DC probe
    {"vo": {"volts": 1, "vrms": 1}},    # two bases for one reading
])
def test_malformed_measurements_are_refused(measured):
    with pytest.raises(CompareError):
        compare_readings(lab1.EXP7_DAC, measured)


def test_a_non_positive_tolerance_is_refused():
    with pytest.raises(CompareError, match="tolerance_pct"):
        compare_readings(lab1.EXP7_DAC, {"vo": -5.0}, tolerance_pct=0)


def test_a_build_that_cannot_be_simulated_is_a_compare_error():
    with pytest.raises(CompareError, match="cannot predict"):
        compare_readings({"supply": {}}, {"vo": 1.0})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_compare.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'circuit_mcp.compare'`.

- [ ] **Step 3: Implement `compare.py`**

Create `src/circuit_mcp/compare.py`:

```python
"""Measured bench readings against what the build says the bench should read.

``expectations`` predicts every probe from the same build the breadboard card
draws. This runs the other direction: given what the student actually read, it
says which probes disagree, by how much, and the likeliest reason, while the
circuit is still on the bench and a wrong probe can still be moved.
"""
from __future__ import annotations

import math
from typing import Any

from .breadboard import BuildError, Probe, Source, parse_build
from .expect import ExpectError, expectations

DEFAULT_TOLERANCE_PCT = 5.0
ZERO_FLOOR_V = 1e-3      # an expected value this close to zero has no meaningful percent error
ABS_TOLERANCE_V = 0.05   # the pass band used instead, in volts
RAIL_MARGIN_V = 0.1      # a reading this close to the chip's output limit is sitting on the rail
AC_BASES = ("vrms", "vpp", "vpk")
_UNITS = {"volts": "V", "vrms": "V RMS", "vpp": "V pk-pk", "vpk": "V peak"}


class CompareError(ValueError):
    """The build cannot be simulated, or the measurements do not fit it."""


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CompareError(f"{where} must be a finite number")
    return float(value)


def _fmt(value: float, basis: str) -> str:
    return f"{value:.4g} {_UNITS[basis]}"


def _reading(label: str, raw: Any, kind: str) -> tuple[str, float]:
    """(basis, value) for one measurement. A bare number is volts for DC, V RMS for AC."""
    if not isinstance(raw, dict):
        return ("volts" if kind == "dc" else "vrms"), _number(raw, f"measured[{label!r}]")
    if len(raw) != 1:
        raise CompareError(f"measured[{label!r}] must name exactly one of volts, vrms, vpp, vpk")
    ((basis, value),) = raw.items()
    allowed = ("volts",) if kind == "dc" else AC_BASES
    if basis not in allowed:
        raise CompareError(f"{label!r} is a {kind.upper()} probe; give its reading as {' or '.join(allowed)}")
    return basis, _number(value, f"measured[{label!r}][{basis!r}]")


def _close(a: float, b: float, tolerance_pct: float) -> bool:
    if abs(b) < ZERO_FLOOR_V:
        return abs(a - b) <= ABS_TOLERANCE_V
    return abs(a - b) <= abs(b) * tolerance_pct / 100


def _judge(expected: float, measured: float, tolerance_pct: float) -> dict[str, Any]:
    if abs(expected) < ZERO_FLOOR_V:
        return {"expected": expected, "measured": measured, "error_pct": None,
                "within_tolerance": abs(measured - expected) <= ABS_TOLERANCE_V}
    error = (measured - expected) / abs(expected) * 100
    return {"expected": expected, "measured": measured, "error_pct": round(error, 2),
            "within_tolerance": abs(error) <= tolerance_pct}


def _peak(basis: str, value: float) -> float:
    """Every reading as a peak (a signed level for DC): the basis predicted gains use."""
    return {"volts": value, "vpk": value, "vpp": value / 2, "vrms": value * math.sqrt(2)}[basis]


def _source_setting(source: Source, basis: str) -> float | None:
    """A source's setting in the basis the probe was read in, or None if not comparable."""
    if source.kind == "dc":
        return source.amplitude if basis == "volts" else None
    if basis == "volts":
        return None
    if source.kind == "sine":        # amplitude is V RMS
        rms = source.amplitude
        return {"vrms": rms, "vpk": rms * math.sqrt(2), "vpp": 2 * math.sqrt(2) * rms}[basis]
    vpp = source.amplitude           # square: amplitude is V pk-pk, and its RMS equals its peak
    return {"vpp": vpp, "vpk": vpp / 2, "vrms": vpp / 2}[basis]


def _hint(probe: Probe, basis: str, measured: float, expected: float, sources: tuple[Source, ...],
          swing: dict[str, float], outputs: set[str], tolerance_pct: float) -> dict[str, str] | None:
    """The likeliest cause of an out-of-tolerance reading, or None if nothing fits."""
    if basis == "volts" and abs(expected) >= ZERO_FLOOR_V and _close(measured, -expected, tolerance_pct):
        return {"kind": "sign",
                "message": f"{probe.label} reads {_fmt(measured, basis)}: the right size with the wrong sign. "
                           "Check the probe polarity, or a minus sign lost when the value was written down."}
    for source in sources:
        setting = _source_setting(source, basis)
        if source.node == probe.node or setting is None or abs(setting) < ZERO_FLOOR_V:
            continue
        if _close(measured, setting, tolerance_pct):
            return {"kind": "source",
                    "message": f"{probe.label} reads {_fmt(measured, basis)}, which is source {source.ref}'s "
                               f"setting ({_fmt(setting, basis)}), not the {_fmt(expected, basis)} expected at "
                               f"{probe.node}. The probe is probably on {source.node} instead of {probe.node}."}
    if probe.node in outputs:
        if basis == "volts":
            on_rail = measured >= swing["high"] - RAIL_MARGIN_V or measured <= swing["low"] + RAIL_MARGIN_V
        elif basis in ("vpk", "vpp"):
            peak = measured if basis == "vpk" else measured / 2
            on_rail = peak >= min(swing["high"], -swing["low"]) - RAIL_MARGIN_V
        else:
            on_rail = False
        if on_rail:
            return {"kind": "rail",
                    "message": f"{probe.label} reads {_fmt(measured, basis)}, at the op amp's output limit "
                               f"({swing['low']:g} V to {swing['high']:g} V on this supply): the output is "
                               "clipping. Lower the input or the gain."}
    return None


def compare_readings(build: Any, measured: Any, tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> dict[str, Any]:
    """Each measured probe against the build's prediction, with the likeliest cause of every miss."""
    if not isinstance(measured, dict) or not measured:
        raise CompareError("measured must map at least one probe label to its reading")
    tolerance = _number(tolerance_pct, "tolerance_pct")
    if tolerance <= 0:
        raise CompareError("tolerance_pct must be positive")
    try:
        parsed = parse_build(build)
        predicted = expectations(build)
    except (BuildError, ExpectError) as exc:
        raise CompareError(f"cannot predict this build: {exc}") from exc
    probes = {probe.label: probe for probe in parsed.probes}
    expected_by_label = {reading["label"]: reading for reading in predicted["readings"]}
    unknown = [label for label in measured if label not in expected_by_label]
    if unknown:
        raise CompareError(f"no probe labelled {', '.join(map(repr, unknown))}; "
                           f"this build's probes are {', '.join(map(repr, expected_by_label))}")
    outputs = {use.out for use in parsed.opamps}
    readings: list[dict[str, Any]] = []
    peaks: dict[str, float] = {}
    for label, raw in measured.items():
        expected = expected_by_label[label]
        basis, value = _reading(label, raw, expected["kind"])
        entry = {"label": label, "basis": basis, **_judge(expected[basis], value, tolerance)}
        if not entry["within_tolerance"]:
            hint = _hint(probes[label], basis, value, expected[basis], parsed.sources,
                         predicted["swing"], outputs, tolerance)
            if hint is not None:
                entry["hint"] = hint
        readings.append(entry)
        peaks[label] = _peak(basis, value)
    gains: list[dict[str, Any]] = []
    for gain in predicted["gains"]:
        out_peak, in_peak = peaks.get(gain["output"]), peaks.get(gain["input"])
        if out_peak is None or in_peak is None or abs(in_peak) < ZERO_FLOOR_V:
            continue
        gains.append({"output": gain["output"], "input": gain["input"],
                      **_judge(gain["gain"], round(out_peak / in_peak, 3), tolerance)})
    return {"ok": True, "tolerance_pct": tolerance, "readings": readings, "gains": gains,
            "all_within_tolerance": all(item["within_tolerance"] for item in readings + gains)}
```

- [ ] **Step 4: Run the module tests to verify they pass**

Run: `uv run python -m pytest tests/test_compare.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing server tests**

In `tests/test_server.py`:
1. Add `compare_readings,` to the `from circuit_mcp.server import (...)` block.
2. Add `from tests.fixtures import lab1` next to the other imports if it is not already imported.
3. Add `"compare_readings",` to `TOOL_NAMES`.
4. Append these tests:

```python
def test_compare_readings_tool_flags_a_dropped_minus_sign():
    result = compare_readings(lab1.EXP7_DAC, {"vo": 5.0})
    assert result["ok"] is True
    assert result["readings"][0]["hint"]["kind"] == "sign"


def test_compare_readings_tool_names_an_unknown_probe_as_a_compare_error():
    result = compare_readings(lab1.EXP7_DAC, {"CH9": 1.0})
    assert result["ok"] is False
    assert result["error"] == "compare_error"
    assert "CH9" in result["message"]
```

In `tests/test_transport.py`, add `"compare_readings",` to the tool-name set that `assert {tool.name for tool in tools.tools} == {...}` compares against.

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run python -m pytest tests/test_server.py tests/test_transport.py -q`
Expected: FAIL — `ImportError: cannot import name 'compare_readings' from 'circuit_mcp.server'`.

- [ ] **Step 7: Wire the tool into `server.py`**

1. Directly under `from .cards import CardError, build_card`, add:

```python
from .compare import CompareError, compare_readings as _compare_readings
```

2. In the `_IMPLEMENTATIONS` dict, directly under `"build_card": _build_card,`, add:

```python
    "compare_readings": _compare_readings,
```

3. In `_ERROR_KINDS`, directly above `(CardError, "card_refused"),`, add:

```python
    (CompareError, "compare_error"),
```

4. Directly after the `canvas_card_add` tool function (after its final `return` line), add:

```python
@server.tool()
def compare_readings(
    build: dict[str, Any], measured: dict[str, Any], tolerance_pct: float = 5.0
) -> dict[str, Any]:
    """Check bench readings against what the build predicts at each probe.

    ``build`` is the same description an ``expected`` card takes. ``measured``
    maps probe labels to readings: a number is volts for a DC probe and V RMS
    for an AC probe (the scope's AC RMS measurement); ``{"vpp": x}`` or
    ``{"vpk": x}`` gives an AC reading in that basis instead. Each probe comes
    back with its expected value, error and pass/fail, and measured gains are
    checked against predicted gains. An out-of-tolerance reading carries a
    hint naming the likeliest cause: a lost minus sign, a probe on a source
    instead of its node, or an output sitting on a rail. An expected 0 V is
    judged with an absolute 0.05 V band instead of a percentage.
    """
    return _guarded("compare_readings", build=build, measured=measured, tolerance_pct=tolerance_pct)
```

- [ ] **Step 8: Document it in the workflow**

In `CLAUDE.md`, in workflow step 9, find the sentence ending "suspect the wiring first, then the pot position, then the model." and append this sentence directly after it (same paragraph, same indentation and line-wrapping style):

```
Put numbers on that with `compare_readings`: pass the same build and the
student's readings, and it reports each probe's error and names a lost minus
sign, a probe on a source instead of its node, or an output on a rail.
```

- [ ] **Step 9: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: all pass (686 baseline plus the new tests).

- [ ] **Step 10: Commit**

```bash
git add src/circuit_mcp/compare.py tests/test_compare.py src/circuit_mcp/server.py tests/test_server.py tests/test_transport.py CLAUDE.md
git commit -m "Check measured bench readings against the build's prediction and name the likeliest cause of each miss."
```

---

### Task 2: summing_dac_output

**Files:**
- Modify: `src/circuit_mcp/course_metrics.py` (new function after `dac_output`; `dac_output` docstring)
- Modify: `tests/test_course_metrics.py` (import, tests)
- Modify: `src/circuit_mcp/server.py` (import, `_IMPLEMENTATIONS`, new tool after the `dac_output` tool, `dac_output` tool docstring)
- Modify: `tests/test_server.py` (import tool, `TOOL_NAMES`, one test)
- Modify: `tests/test_transport.py` (tool-name set)

**Interfaces:**
- Consumes: `MetricsError` in `course_metrics.py` (already mapped to `metrics_error`).
- Produces: `circuit_mcp.course_metrics.summing_dac_output(codes, bits, r_feedback, r_bits, v_logic=1.0, tolerance_pct=5.0) -> dict`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_course_metrics.py`, add `summing_dac_output,` to the existing `from circuit_mcp.course_metrics import (...)` block, then append:

```python
def test_summing_dac_with_nominal_resistors_is_the_ideal_binary_ladder():
    dac = summing_dac_output(list(range(8)), 3, 10e3, [2.5e3, 5e3, 10e3])
    assert [row["output_v"] for row in dac["outputs"]] == pytest.approx([0, -1, -2, -3, -4, -5, -6, -7])
    assert [row["ideal_v"] for row in dac["outputs"]] == [0, -1, -2, -3, -4, -5, -6, -7]
    assert [row["binary"] for row in dac["outputs"]][5] == "101"
    assert dac["weights"] == pytest.approx([4, 2, 1])
    assert dac["ideal_weights"] == [4, 2, 1]
    assert dac["all_within_tolerance"] is True


def test_summing_dac_with_lab1_measured_resistors():
    # Exp 7 as built: Rf 9.78k, RD2 2.39k, RD1 4.87k, RD0 9.79k. Units cancel, so kilohms are fine.
    row = summing_dac_output([5], 3, 9.78, [2.39, 4.87, 9.79])["outputs"][0]
    assert row["output_v"] == pytest.approx(-5.091, abs=0.001)
    assert row["error_pct"] == pytest.approx(-1.82, abs=0.01)
    assert row["within_tolerance"] is True


def test_summing_dac_flags_a_code_outside_tolerance():
    dac = summing_dac_output([4], 3, 10e3, [2.0e3, 5e3, 10e3])   # MSB resistor 20 % low
    row = dac["outputs"][0]
    assert row["output_v"] == pytest.approx(-5.0)
    assert row["error_pct"] == pytest.approx(-25.0)
    assert row["within_tolerance"] is False
    assert dac["all_within_tolerance"] is False


def test_summing_dac_code_zero_is_positive_zero_with_no_percent_error():
    row = summing_dac_output([0], 3, 10e3, [2.5e3, 5e3, 10e3])["outputs"][0]
    assert row["output_v"] == 0.0
    assert str(row["output_v"]) == "0.0"
    assert row["error_pct"] is None
    assert row["within_tolerance"] is True


@pytest.mark.parametrize(("args", "kwargs"), [
    (([8], 3, 10e3, [2.5e3, 5e3, 10e3]), {}),            # code out of range
    (([1], 3, 10e3, [2.5e3, 5e3]), {}),                  # one resistor short
    (([1], 3, 0.0, [2.5e3, 5e3, 10e3]), {}),             # zero feedback resistor
    (([1], 3, 10e3, [2.5e3, -5e3, 10e3]), {}),           # negative resistor
    (([1], 0, 10e3, []), {}),                            # no bits
    (([], 3, 10e3, [2.5e3, 5e3, 10e3]), {}),             # no codes
    (([1], 3, 10e3, [2.5e3, 5e3, 10e3]), {"v_logic": 0.0}),
    (([1], 3, 10e3, [2.5e3, 5e3, 10e3]), {"tolerance_pct": 0.0}),
])
def test_summing_dac_rejects_unphysical_inputs(args, kwargs):
    with pytest.raises(MetricsError):
        summing_dac_output(*args, **kwargs)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_course_metrics.py -q`
Expected: FAIL — `ImportError: cannot import name 'summing_dac_output'`.

- [ ] **Step 3: Implement `summing_dac_output`**

In `src/circuit_mcp/course_metrics.py`, replace the one-line docstring of `dac_output` with:

```python
    """Ideal straight-binary DAC output over a span: v_min + code * (v_max - v_min) / 2**bits.

    For an op-amp summing-amplifier DAC built from resistors, use
    ``summing_dac_output``: it is inverting and weights each bit by Rf/Ri.
    """
```

Then add this function directly after `dac_output`:

```python
def summing_dac_output(
    codes: list[int],
    bits: int,
    r_feedback: float,
    r_bits: list[float],
    v_logic: float = 1.0,
    tolerance_pct: float = 5.0,
) -> dict[str, Any]:
    """An inverting op-amp summing DAC from its actual resistors, MSB first.

    ``vo = -v_logic * sum(Rf / R_i * b_i)``. The ideal is the same converter
    with exact binary weights, ``-v_logic * code``, so each output reports how
    far the real resistors pull it from ideal. Resistances may be in any one
    unit: only their ratios matter.
    """
    if not 1 <= bits <= 16:
        raise MetricsError("bits must be between 1 and 16")
    if len(r_bits) != bits:
        raise MetricsError(f"r_bits needs one resistor per bit, most significant first: expected {bits}, got {len(r_bits)}")
    if any(not math.isfinite(r) or r <= 0 for r in (r_feedback, *r_bits)):
        raise MetricsError("r_feedback and every r_bits value must be finite and positive")
    if not math.isfinite(v_logic) or v_logic <= 0:
        raise MetricsError("v_logic must be finite and positive")
    if not math.isfinite(tolerance_pct) or tolerance_pct <= 0:
        raise MetricsError("tolerance_pct must be finite and positive")
    levels = 1 << bits
    if not codes or len(codes) > levels or any(type(code) is not int or not 0 <= code < levels for code in codes):
        raise MetricsError(f"codes must list integers from 0 through {levels - 1}")
    weights = [r_feedback / r for r in r_bits]
    outputs = []
    for code in codes:
        on = [(code >> (bits - 1 - i)) & 1 for i in range(bits)]
        output = -v_logic * sum(w * b for w, b in zip(weights, on)) + 0.0   # + 0.0 turns -0.0 into 0.0
        ideal = -v_logic * code + 0.0
        if code == 0:
            error_pct, within = None, abs(output) <= 1e-12
        else:
            error_pct = (output - ideal) / abs(ideal) * 100
            within = abs(error_pct) <= tolerance_pct
            error_pct = round(error_pct, 3)
        outputs.append({"code": code, "binary": format(code, f"0{bits}b"), "output_v": round(output, 6),
                        "ideal_v": ideal, "error_pct": error_pct, "within_tolerance": within})
    return {
        "ok": True,
        "bits": bits,
        "v_logic": v_logic,
        "tolerance_pct": tolerance_pct,
        "weights": [round(w, 6) for w in weights],
        "ideal_weights": [1 << (bits - 1 - i) for i in range(bits)],
        "outputs": outputs,
        "all_within_tolerance": all(row["within_tolerance"] for row in outputs),
    }
```

Note on `ideal_v` for the nominal test: `-1.0 * 3 + 0.0 == -3.0`, and `[0.0, -1.0, ...] == [0, -1, ...]` holds in Python, so the exact-equality assertion on `ideal_v` passes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_course_metrics.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing server tests**

In `tests/test_server.py`: add `summing_dac_output,` to the `from circuit_mcp.server import (...)` block, add `"summing_dac_output",` to `TOOL_NAMES`, and append:

```python
def test_summing_dac_output_tool_reports_each_code():
    result = summing_dac_output([5], 3, 9.78, [2.39, 4.87, 9.79])
    assert result["ok"] is True
    assert result["outputs"][0]["output_v"] == pytest.approx(-5.091, abs=0.001)


def test_summing_dac_output_tool_names_bad_input_as_a_metrics_error():
    result = summing_dac_output([1], 3, 10e3, [2.5e3, 5e3])
    assert result["ok"] is False
    assert result["error"] == "metrics_error"
```

In `tests/test_transport.py`, add `"summing_dac_output",` to the tool-name set.

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run python -m pytest tests/test_server.py tests/test_transport.py -q`
Expected: FAIL — `ImportError: cannot import name 'summing_dac_output' from 'circuit_mcp.server'`.

- [ ] **Step 7: Wire the tool into `server.py`**

1. In the `from .course_metrics import (...)` block, add `summing_dac_output as _summing_dac_output,` (keep the block's alphabetical order).
2. In `_IMPLEMENTATIONS`, directly under `"dac_output": _dac_output,`, add `"summing_dac_output": _summing_dac_output,`.
3. Replace the `dac_output` tool's docstring with:

```python
    """Map ideal straight-binary DAC codes to output voltages over a span.

    Output is v_min + code * (v_max - v_min) / 2**bits. For an op-amp
    summing-amplifier DAC built from resistors (inverting, bits weighted by
    Rf/Ri), use ``summing_dac_output`` instead.
    """
```

4. Directly after the `dac_output` tool function, add:

```python
@server.tool()
def summing_dac_output(
    codes: list[int],
    bits: int,
    r_feedback: float,
    r_bits: list[float],
    v_logic: float = 1.0,
    tolerance_pct: float = 5.0,
) -> dict[str, Any]:
    """Outputs of an inverting op-amp summing DAC, from its actual resistors.

    ``r_bits`` lists one input resistor per bit, most significant first, in
    the same unit as ``r_feedback``. Each code returns the output
    ``-v_logic * sum(Rf/Ri * bit_i)``, the ideal ``-v_logic * code``, the
    percent error and whether it sits within ``tolerance_pct`` of ideal. Use
    the measured resistor values to predict what the bench should read.
    """
    return _guarded("summing_dac_output", codes=list(codes), bits=bits, r_feedback=r_feedback,
                    r_bits=list(r_bits), v_logic=v_logic, tolerance_pct=tolerance_pct)
```

- [ ] **Step 8: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: all pass. `scripts/stress_ee230.py` is not part of the suite and calls `dac_output` unchanged.

- [ ] **Step 9: Commit**

```bash
git add src/circuit_mcp/course_metrics.py tests/test_course_metrics.py src/circuit_mcp/server.py tests/test_server.py tests/test_transport.py
git commit -m "Predict an op-amp summing DAC from its actual resistors, with each code's error against ideal."
```

---

### Task 3: page_segment (expression boxes on a page)

**Files:**
- Create: `src/circuit_mcp/page_segment.py`
- Create: `tests/test_page_segment.py`

**Interfaces:**
- Produces: `page_segment.expression_boxes(gray: np.ndarray) -> list[tuple[int, int, int, int]]` — `(x0, y0, x1, y1)` with exclusive ends, reading order; `page_segment.ink_mask(gray) -> np.ndarray` (raises `ValueError` for non-2-D or empty input). Task 4's OCR worker imports this module BOTH as `circuit_mcp.page_segment` and as top-level `page_segment`, so the module must import only numpy and the standard library and must not use relative imports.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_segment.py`:

```python
"""Finding handwritten expressions on a page, on synthetic pages with known layout."""
from __future__ import annotations

import numpy as np
import pytest

from circuit_mcp.page_segment import PAD, expression_boxes


def page(height=300, width=400):
    return np.full((height, width), 255, np.uint8)


def ink(image, x0, y0, x1, y1):
    image[y0:y1, x0:x1] = 0


def contains(box, x0, y0, x1, y1):
    bx0, by0, bx1, by1 = box
    return bx0 <= x0 and by0 <= y0 and bx1 >= x1 and by1 >= y1


def test_a_blank_page_has_no_expressions():
    assert expression_boxes(page()) == []


def test_two_separate_lines_are_two_boxes_top_first():
    image = page()
    ink(image, 50, 40, 150, 70)
    ink(image, 60, 130, 200, 160)
    boxes = expression_boxes(image)
    assert len(boxes) == 2
    assert contains(boxes[0], 50, 40, 150, 70)
    assert contains(boxes[1], 60, 130, 200, 160)
    assert boxes[0] == (50 - PAD, 40 - PAD, 150 + PAD, 70 + PAD)


def test_a_fraction_stays_one_expression():
    image = page()
    ink(image, 100, 50, 200, 70)    # numerator
    ink(image, 90, 75, 210, 78)     # fraction bar
    ink(image, 100, 82, 200, 102)   # denominator
    boxes = expression_boxes(image)
    assert len(boxes) == 1
    assert contains(boxes[0], 90, 50, 210, 102)


def test_gaps_between_words_do_not_split_a_line():
    image = page()
    for x0, x1 in ((20, 80), (100, 160), (185, 260)):
        ink(image, x0, 40, x1, 70)
    assert len(expression_boxes(image)) == 1


def test_a_wide_gap_splits_a_side_column_left_first():
    image = page()
    ink(image, 20, 40, 120, 70)
    ink(image, 300, 40, 380, 70)
    boxes = expression_boxes(image)
    assert len(boxes) == 2
    assert contains(boxes[0], 20, 40, 120, 70)
    assert contains(boxes[1], 300, 40, 380, 70)


def test_reading_order_is_top_to_bottom_before_left_to_right():
    image = page()
    ink(image, 300, 40, 380, 70)    # upper line, right side
    ink(image, 20, 130, 120, 160)   # lower line, left side
    boxes = expression_boxes(image)
    assert boxes[0][1] < boxes[1][1]


def test_a_speck_is_not_writing():
    image = page()
    ink(image, 50, 40, 150, 70)
    ink(image, 300, 250, 302, 252)
    assert len(expression_boxes(image)) == 1


def test_a_dark_page_with_light_ink_segments_the_same():
    image = page()
    ink(image, 50, 40, 150, 70)
    ink(image, 60, 130, 200, 160)
    assert expression_boxes(255 - image) == expression_boxes(image)


def test_boxes_are_clipped_to_the_page():
    image = page()
    ink(image, 0, 0, 50, 20)
    (box,) = expression_boxes(image)
    assert box[0] == 0 and box[1] == 0
    assert box[2] <= image.shape[1] and box[3] <= image.shape[0]


@pytest.mark.parametrize("bad", [np.zeros((10, 10, 3), np.uint8), np.zeros((0, 5), np.uint8)])
def test_only_a_non_empty_grayscale_image_is_accepted(bad):
    with pytest.raises(ValueError, match="2-D grayscale"):
        expression_boxes(bad)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_page_segment.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'circuit_mcp.page_segment'`.

- [ ] **Step 3: Implement `page_segment.py`**

Create `src/circuit_mcp/page_segment.py`:

```python
"""Where the handwritten expressions are on a page, for the formula recognizer.

UniMERNet reads one tightly cropped expression, but a student's working arrives
as a whole scanned page. Rows of ink make lines; lines closer than half a
typical line height stay one expression (a fraction's numerator, bar and
denominator); and a horizontal gap several line heights wide splits a line into
separate columns, such as a side calculation next to the main working.

Only numpy and the standard library, and no relative imports: the OCR worker is
run as a script and imports this module by file name, outside the package.
"""
from __future__ import annotations

import numpy as np

MERGE_FRACTION = 0.5            # a vertical gap under this share of the median line height joins lines
SPLIT_LINE_HEIGHTS = 3.0        # a horizontal gap this many median line heights wide splits columns
SPLIT_MIN_WIDTH_FRACTION = 0.08  # ...and never narrower than this share of the page width
MIN_INK_PIXELS = 12             # fewer ink pixels than this is a speck, not writing
INK_CONTRAST = 96               # how far from the background a pixel must be to count as ink
PAD = 6                         # margin kept around each box, in pixels

Box = tuple[int, int, int, int]


def ink_mask(gray: np.ndarray) -> np.ndarray:
    """True where there is writing, on a light or a dark background."""
    image = np.asarray(gray)
    if image.ndim != 2 or image.size == 0:
        raise ValueError("expected a non-empty 2-D grayscale image")
    image = image.astype(np.int16)
    background = int(np.median(image))
    return np.abs(image - background) >= INK_CONTRAST


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, end) runs of True in a 1-D boolean array."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


def _join(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Merge consecutive runs separated by less than ``gap``."""
    joined: list[list[int]] = []
    for start, end in runs:
        if joined and start - joined[-1][1] < gap:
            joined[-1][1] = end
        else:
            joined.append([start, end])
    return [(start, end) for start, end in joined]


def expression_boxes(gray: np.ndarray) -> list[Box]:
    """``(x0, y0, x1, y1)`` boxes with exclusive ends, top to bottom then left to right."""
    ink = ink_mask(gray)
    height, width = ink.shape
    rows = _runs(ink.any(axis=1))
    if not rows:
        return []
    line_height = float(np.median([end - start for start, end in rows]))
    bands = _join(rows, max(2, int(MERGE_FRACTION * line_height)))
    split_gap = max(int(SPLIT_LINE_HEIGHTS * line_height), int(SPLIT_MIN_WIDTH_FRACTION * width), 1)
    boxes: list[Box] = []
    for top, bottom in bands:
        band = ink[top:bottom]
        for left, right in _join(_runs(band.any(axis=0)), split_gap):
            cell = band[:, left:right]
            if int(cell.sum()) < MIN_INK_PIXELS:
                continue
            ys = np.flatnonzero(cell.any(axis=1))
            y0, y1 = top + int(ys[0]), top + int(ys[-1]) + 1
            boxes.append((max(0, left - PAD), max(0, y0 - PAD), min(width, right + PAD), min(height, y1 + PAD)))
    return boxes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_page_segment.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/circuit_mcp/page_segment.py tests/test_page_segment.py
git commit -m "Find each handwritten expression on a scanned page so the formula recognizer can read them one at a time."
```

---

### Task 4: transcribe_page (worker action and MCP tool)

**Files:**
- Modify: `src/circuit_mcp/ocr_worker.py` (dual import of `page_segment`, `MAX_PAGE_EXPRESSIONS`, split `transcribe` into `_decode` + `_latex`, new `transcribe_page`, dispatch in `serve`)
- Create: `tests/test_ocr_worker.py`
- Modify: `src/circuit_mcp/server.py` (`PAGE_OCR_TIMEOUT_SECONDS`, new tool after `transcribe_image`)
- Modify: `tests/test_server.py` (import tool, `TOOL_NAMES`, two tests)
- Modify: `tests/test_transport.py` (tool-name set)
- Modify: `CLAUDE.md` (one sentence in the privacy-scoped image paragraph)

**Interfaces:**
- Consumes: `page_segment.expression_boxes` from Task 3. `OCR_WORKER.call(request: dict, timeout: float = OCR_TIMEOUT_SECONDS) -> dict` in `ocr_client.py`; `_decode_image` and `_transcription_content` in `server.py`.
- Produces: worker action `{"action": "transcribe_page", "png": bytes}` → `{"ok": True, "expressions": [{"index", "bbox": [x0, y0, x1, y1], "latex"}], "expression_count", "truncated", "device", "model", "image_width", "image_height", "inference_seconds"}`; MCP tool `transcribe_page(image_base64) -> CallToolResult`.

- [ ] **Step 1: Write the failing worker tests**

Create `tests/test_ocr_worker.py`:

```python
"""The OCR worker's page action, with the recognizer replaced by a stub.

UniMERNet cannot run here, so ``_latex`` is stubbed; the tests cover decoding,
segmentation, cropping, ordering and the expression cap. PIL is the worker's
own dependency, so these tests skip where it is not installed.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from circuit_mcp import ocr_worker

Image = pytest.importorskip("PIL.Image")


def png(array):
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def engine(monkeypatch):
    engine = ocr_worker._Engine("unimernet_test", "cpu")
    engine.device = "cpu"
    monkeypatch.setattr(engine, "load", lambda: None)
    seen = []

    def fake_latex(image):
        seen.append(image.size)
        return f"expr{len(seen)}", 0.01

    monkeypatch.setattr(engine, "_latex", fake_latex)
    engine.seen = seen
    return engine


def test_page_transcription_crops_each_expression_in_reading_order(engine):
    page = np.full((300, 400), 255, np.uint8)
    page[40:70, 50:150] = 0
    page[130:160, 60:200] = 0
    result = engine.transcribe_page(png(page))
    assert result["ok"] is True
    assert [e["latex"] for e in result["expressions"]] == ["expr1", "expr2"]
    assert [e["index"] for e in result["expressions"]] == [0, 1]
    first, second = (e["bbox"] for e in result["expressions"])
    assert first[1] < second[1]
    assert engine.seen[0] == (first[2] - first[0], first[3] - first[1])   # the crop, not the page
    assert result["expression_count"] == 2
    assert result["truncated"] is False
    assert (result["image_width"], result["image_height"]) == (400, 300)


def test_page_transcription_caps_the_expression_count(engine):
    page = np.full((2100, 200), 255, np.uint8)
    for i in range(70):
        page[i * 30 : i * 30 + 10, 20:120] = 0
    result = engine.transcribe_page(png(page))
    assert result["expression_count"] == 70
    assert len(result["expressions"]) == ocr_worker.MAX_PAGE_EXPRESSIONS
    assert result["truncated"] is True


def test_an_undecodable_page_is_a_value_error(engine):
    with pytest.raises(ValueError, match="Could not decode"):
        engine.transcribe_page(b"\x89PNG\r\n\x1a\nnot really")


def test_the_worker_script_imports_page_segment_outside_the_package():
    worker = Path(ocr_worker.__file__)
    run = subprocess.run([sys.executable, str(worker)], capture_output=True, text=True, timeout=60)
    assert "usage: ocr_worker.py" in run.stderr
    assert "Error" not in run.stderr.replace("SystemExit", "")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run python -m pytest tests/test_ocr_worker.py -q`
Expected: FAIL — `AttributeError: '_Engine' object has no attribute 'transcribe_page'` (and `MAX_PAGE_EXPRESSIONS` missing).

- [ ] **Step 3: Implement the worker action**

In `src/circuit_mcp/ocr_worker.py`:

1. After the existing standard-library imports, add:

```python
try:
    from . import page_segment   # imported as circuit_mcp.ocr_worker, by the tests and tooling
except ImportError:              # run as a script by OCRWorker: this file's directory is sys.path[0]
    import page_segment

MAX_PAGE_EXPRESSIONS = 60
```

2. Replace the whole `transcribe` method with these four methods (behaviour of `transcribe` is unchanged; `inference_seconds` still times only generation):

```python
    def _decode(self, png: bytes):
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(png)).convert("RGB")
            image.load()
        except Exception as exc:
            raise ValueError(f"Could not decode input image: {exc}") from exc
        return image

    def _latex(self, image) -> tuple[str, float]:
        """One cropped expression -> (LaTeX, seconds spent generating)."""
        import torch

        tensor = self.processor(image).unsqueeze(0).to(self.device)
        started = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(
                {"image": tensor}, temperature=0.0, do_sample=False
            )
        return output["pred_str"][0].strip(), time.monotonic() - started

    def transcribe(self, png: bytes) -> dict:
        self.load()
        image = self._decode(png)
        width, height = image.size
        latex, seconds = self._latex(image)
        return {
            "ok": True,
            "latex": latex,
            "device": self.device,
            "model": self.model_dir.name,
            "image_width": width,
            "image_height": height,
            "inference_seconds": seconds,
        }

    def transcribe_page(self, png: bytes) -> dict:
        """Every expression on a page, in reading order, each with its box."""
        import numpy as np

        self.load()
        image = self._decode(png)
        width, height = image.size
        boxes = page_segment.expression_boxes(np.asarray(image.convert("L")))
        expressions, seconds = [], 0.0
        for index, box in enumerate(boxes[:MAX_PAGE_EXPRESSIONS]):
            latex, spent = self._latex(image.crop(box))
            seconds += spent
            expressions.append({"index": index, "bbox": list(box), "latex": latex})
        return {
            "ok": True,
            "expressions": expressions,
            "expression_count": len(boxes),
            "truncated": len(boxes) > MAX_PAGE_EXPRESSIONS,
            "device": self.device,
            "model": self.model_dir.name,
            "image_width": width,
            "image_height": height,
            "inference_seconds": seconds,
        }
```

3. In `serve`, directly after the `elif action == "transcribe":` branch, add:

```python
            elif action == "transcribe_page":
                response = engine.transcribe_page(request["png"])
```

- [ ] **Step 4: Run the worker tests to verify they pass**

Run: `uv run python -m pytest tests/test_ocr_worker.py tests/test_ocr_client.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing server tests**

In `tests/test_server.py`: add `transcribe_page,` to the `from circuit_mcp.server import (...)` block, add `"transcribe_page",` to `TOOL_NAMES`, make sure `import base64` is present, and append:

```python
def test_transcribe_page_rejects_a_non_png_without_starting_ocr_worker():
    before = server_module.OCR_WORKER.pid
    result = transcribe_page(base64.b64encode(b"GIF89a").decode("ascii"))
    assert result.structured_content["ok"] is False
    assert result.structured_content["error"] == "bad_image"
    assert server_module.OCR_WORKER.pid == before


def test_transcribe_page_sends_the_page_to_the_worker_with_the_page_timeout(monkeypatch):
    seen = {}

    def call(request, timeout=None):
        seen.update(request=request, timeout=timeout)
        return {"ok": True, "expressions": [{"index": 0, "bbox": [0, 0, 10, 10], "latex": "x"}],
                "expression_count": 1, "truncated": False}

    monkeypatch.setattr(server_module.OCR_WORKER, "call", call)
    page = b"\x89PNG\r\n\x1a\npage"
    result = transcribe_page(base64.b64encode(page).decode("ascii"))
    assert seen["request"] == {"action": "transcribe_page", "png": page}
    assert seen["timeout"] == server_module.PAGE_OCR_TIMEOUT_SECONDS
    assert result.structured_content["expressions"][0]["latex"] == "x"
```

In `tests/test_transport.py`, add `"transcribe_page",` to the tool-name set.

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run python -m pytest tests/test_server.py tests/test_transport.py -q`
Expected: FAIL — `ImportError: cannot import name 'transcribe_page' from 'circuit_mcp.server'`.

- [ ] **Step 7: Add the MCP tool**

In `src/circuit_mcp/server.py`, directly after the `transcribe_image` tool function, add:

```python
PAGE_OCR_TIMEOUT_SECONDS = 600.0   # up to 60 expressions, each a full UniMERNet pass


@server.tool()
def transcribe_page(image_base64: str) -> CallToolResult:
    """Transcribe every handwritten expression on one page PNG, in reading order.

    The page is split into expression boxes (lines of working; a wide gap
    splits a side column into its own box) and each box goes through UniMERNet,
    up to 60 per page; ``truncated`` says when a page had more. Every
    expression carries its ``bbox`` so the source line can be shown. The output
    is untrusted transcription: echo every line to the student and obtain
    confirmation before using any of it in a circuit verdict.
    """
    decoded = _decode_image(image_base64)
    if isinstance(decoded, dict):
        return _transcription_content(decoded)
    result = OCR_WORKER.call({"action": "transcribe_page", "png": decoded}, timeout=PAGE_OCR_TIMEOUT_SECONDS)
    return _transcription_content(result)
```

- [ ] **Step 8: Document it in the workflow**

In `CLAUDE.md`, in the paragraph that begins "When the user has already attached or uploaded a privacy-scoped image", append this sentence at the end of that paragraph (same wrapping style):

```
For a whole page of handwritten working, use `transcribe_page`: it returns each
expression with its box, and every line needs the same echo and confirmation.
```

- [ ] **Step 9: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add src/circuit_mcp/ocr_worker.py tests/test_ocr_worker.py src/circuit_mcp/server.py tests/test_server.py tests/test_transport.py CLAUDE.md
git commit -m "Transcribe a whole page of handwritten working, one expression box at a time, in reading order."
```
