# Breadboard Build and Bench Expectations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a confirmed circuit description, put two cards on the desk: a breadboard layout you can wire from (chip, parts, jumpers, rails, where the generator and probes go) and the readings the bench should show at each probe (DC volts, AC peak/rms, waveform, clipping, gain and phase).

**Architecture:** One JSON *build* description is the single source of truth. `breadboard.py` turns it into a deterministic placement that is verified by union-find before it is drawn as SVG; `expect.py` turns the same build into an ngspice deck with a rail-limited op amp and summarises the result per probe. Both are new kinds of canvas card, so the existing `canvas_card_add` tool, storage, polling, and rendering carry them. The agent supplies the build only after the student has confirmed the netlist, exactly as `CLAUDE.md` already requires for every verdict.

**Tech Stack:** Python 3.12, dataclasses, hand-built SVG strings (no library), ngspice 47 via the existing `spice.simulate_spice`, pytest. No new dependencies.

## Global Constraints

- Write the failing test first, then the implementation; run the tests before calling a task done.
- No new packages. Everything here is stdlib plus what the repo already installs.
- Fail loud: every refusal is a `BuildError`, `ExpectError`, or `PartError` with a message naming the part or field; no swallowed exceptions, no silent fallbacks.
- No print statements or debug artifacts left in code.
- Text from the agent (refs, node names, labels) is escaped before it reaches SVG; SVG produced by the server is the only markup the browser inserts.
- A layout is returned only if `verify()` passes; a wrong drawing is worse than no drawing.
- The pin map in `parts.py` is data checked against the datasheet numbers in a test, never inferred.
- Every commit message ends with the two attribution lines the session requires.

## File Structure

| File | Responsibility |
|---|---|
| `src/circuit_mcp/parts.py` (new) | Chip pin maps, component kinds, value parsing, LED and op-amp SPICE models |
| `src/circuit_mcp/breadboard.py` (new) | Build parsing and validation, placement, verification, wire list, SVG |
| `src/circuit_mcp/expect.py` (new) | ngspice deck from a build, run, per-probe readings, gain and phase |
| `src/circuit_mcp/cards.py` | Two new card kinds: `breadboard`, `expected` |
| `src/circuit_mcp/storage.py` | `CARD_KINDS` accepts the two kinds |
| `src/circuit_mcp/server.py` | `canvas_card_add` docstring documents the build format |
| `src/circuit_mcp/static/app.js`, `canvas.css` | Render the two kinds; keep them across reload |
| `tests/fixtures/lab1.py` (new) | The eight Lab 1 circuits as builds |
| `tests/test_parts.py`, `tests/test_breadboard.py`, `tests/test_expect.py` (new) | Unit and acceptance tests |
| `CLAUDE.md`, `README.md` | Workflow step and user documentation |

The build description every task shares (parsed by Task 2, consumed by Tasks 3 to 6):

```json
{
  "supply": {"vplus": 15, "vminus": -15},
  "chips": [{"ref": "U1", "part": "LM324"}],
  "parts": [
    {"ref": "R3", "kind": "pot", "value": "10k", "nodes": ["vsine", "vi", "gnd"]},
    {"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["fb", "gnd"]},
    {"ref": "R2", "kind": "resistor", "value": "15k", "nodes": ["out", "fb"]}
  ],
  "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "vi", "inn": "fb", "out": "out"}],
  "sources": [{"ref": "VS", "kind": "sine", "node": "vsine", "vrms": 1.0, "freq": 1000}],
  "probes": [{"label": "CH1 vi", "node": "vi", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
  "pot_positions": {"R3": 0.5}
}
```

Node `gnd` (aliases `0`, `ground`) is the supply common. `vplus` and `vminus` are reserved for the rails. A pot's nodes are `[end, wiper, end]`; an LED's are `[anode, cathode]`. `vminus: 0` means single supply. Source kinds: `dc` (`volts`), `sine` (`vrms`, `freq`), `square` (`vpp`, `freq`).

---

### Task 1: Part library

**Files:**
- Create: `src/circuit_mcp/parts.py`
- Test: `tests/test_parts.py`

**Interfaces:**
- Produces: `chip(part: str) -> Chip` with `Chip.pins`, `.vplus`, `.vminus`, `.headroom_high`, `.headroom_low`, `.section(name) -> OpAmpSection(out, inn, inp)`; `parse_value(text) -> float`; `TWO_TERMINAL: dict[kind, spice_prefix]`; `UNITS`; `led_saturation_current()`; `opamp_subcircuit() -> str`; `PartError`.

- [ ] **Step 1: Write the failing tests**

```python
"""The part library: pin numbers a student wires from, checked against the datasheet."""
from __future__ import annotations

import math

import pytest

from circuit_mcp.parts import (
    CHIPS, QUAD_DIP14, PartError, chip, led_saturation_current, opamp_subcircuit, parse_value,
)


def test_quad_dip14_pin_map_matches_the_datasheet():
    # LM324 and LMC660 datasheets: OUT1=1, IN1-=2, IN1+=3, V+=4, IN2+=5, IN2-=6, OUT2=7,
    # OUT3=8, IN3-=9, IN3+=10, V-=11, IN4+=12, IN4-=13, OUT4=14.
    expected = {"A": (1, 2, 3), "B": (7, 6, 5), "C": (8, 9, 10), "D": (14, 13, 12)}
    assert {s.name: (s.out, s.inn, s.inp) for s in QUAD_DIP14} == expected
    for name in ("LM324", "LMC660"):
        assert (CHIPS[name].vplus, CHIPS[name].vminus, CHIPS[name].pins) == (4, 11, 14)


def test_every_pin_of_the_quad_package_is_used_exactly_once():
    pins = [4, 11] + [p for s in QUAD_DIP14 for p in (s.out, s.inn, s.inp)]
    assert sorted(pins) == list(range(1, 15))


def test_chip_lookup_is_case_insensitive_and_loud():
    assert chip("lm324").part == "LM324"
    with pytest.raises(PartError, match="unknown chip"):
        chip("NE555")


def test_section_lookup_is_case_insensitive_and_loud():
    assert chip("LMC660").section("b").out == 7
    with pytest.raises(PartError, match="no section"):
        chip("LMC660").section("E")


def test_the_lm324_cannot_reach_its_positive_rail_but_the_lmc660_nearly_can():
    assert chip("LM324").headroom_high == 1.5
    assert chip("LMC660").headroom_high <= 0.1


@pytest.mark.parametrize("text,value", [
    ("1k", 1e3), ("15k", 15e3), ("2.2k", 2200.0), ("0.1u", 1e-7), ("10n", 1e-8),
    ("470k", 470e3), ("250m", 0.25), ("1meg", 1e6), ("100", 100.0), ("4.7 kΩ", 4700.0),
])
def test_component_values_parse(text, value):
    assert math.isclose(parse_value(text), value, rel_tol=1e-9)


@pytest.mark.parametrize("bad", ["", "k", "1x", "ten", "1,000", None])
def test_unreadable_values_are_refused(bad):
    with pytest.raises(PartError, match="cannot read"):
        parse_value(bad)


def test_led_model_puts_two_volts_across_the_diode_at_three_milliamps():
    saturation = led_saturation_current(2.0, 3e-3, 2.0)
    current = saturation * math.exp(2.0 / (2.0 * 0.02585))
    assert math.isclose(current, 3e-3, rel_tol=1e-9)


def test_opamp_subcircuit_clamps_with_named_headroom():
    text = opamp_subcircuit()
    assert ".subckt railamp inp inn out vp vn params: hi=1.5 lo=0.5" in text
    assert "{hi}" in text and "{lo}" in text
    assert text.strip().endswith(".ends railamp")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_parts.py -q`
Expected: `ModuleNotFoundError: No module named 'circuit_mcp.parts'`

- [ ] **Step 3: Write the module**

```python
"""Physical parts shared by the breadboard layout and the bench simulator.

The pin map is the one thing here that must never be wrong: a pin number off
by one sends a student wiring into the wrong leg of the chip. It is data, not
inference, and the tests check it against the datasheet numbers.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


class PartError(ValueError):
    """A part, pin, or value the library does not know."""


@dataclass(frozen=True)
class OpAmpSection:
    name: str
    out: int
    inn: int
    inp: int


@dataclass(frozen=True)
class Chip:
    part: str
    pins: int
    vplus: int
    vminus: int
    sections: tuple[OpAmpSection, ...]
    headroom_high: float  # volts the output stops short of V+
    headroom_low: float   # volts the output stops short of V-

    def section(self, name: str) -> OpAmpSection:
        for section in self.sections:
            if section.name == name.upper():
                return section
        names = ", ".join(s.name for s in self.sections)
        raise PartError(f"{self.part} has no section {name!r}; sections are {names}")


# Industry-standard quad op amp DIP-14: V+ pin 4, V- pin 11.
QUAD_DIP14 = (
    OpAmpSection("A", out=1, inn=2, inp=3),
    OpAmpSection("B", out=7, inn=6, inp=5),
    OpAmpSection("C", out=8, inn=9, inp=10),
    OpAmpSection("D", out=14, inn=13, inp=12),
)

CHIPS: dict[str, Chip] = {
    # LM324: output reaches V+ - 1.5 V; low side is within tens of mV of V- when
    # sinking little current, 0.5 V is a conservative figure for a loaded output.
    "LM324": Chip("LM324", 14, 4, 11, QUAD_DIP14, headroom_high=1.5, headroom_low=0.5),
    # LMC660: rail-to-rail output.
    "LMC660": Chip("LMC660", 14, 4, 11, QUAD_DIP14, headroom_high=0.1, headroom_low=0.1),
}


def chip(part: str) -> Chip:
    try:
        return CHIPS[part.upper()]
    except KeyError as exc:
        raise PartError(f"unknown chip {part!r}; known: {', '.join(sorted(CHIPS))}") from exc


TWO_TERMINAL = {"resistor": "R", "capacitor": "C", "inductor": "L"}
UNITS = {"resistor": "Ω", "capacitor": "F", "inductor": "H"}

_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "meg": 1e6, "M": 1e6, "g": 1e9}
_VALUE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(meg|[pnuµmkMg])?\s*[A-Za-zΩ]*\s*$")


def parse_value(text: str) -> float:
    """'15k' -> 15000.0, '0.1u' -> 1e-7, '470k' -> 470000.0. Loud on anything else."""
    match = _VALUE.match(str(text))
    if not match:
        raise PartError(f"cannot read component value {text!r}")
    number, prefix = match.groups()
    return float(number) * (_SI[prefix] if prefix else 1.0)


THERMAL_VOLTAGE = 0.02585


def led_saturation_current(forward_volts: float = 2.0, at_amps: float = 3e-3, emission: float = 2.0) -> float:
    """Diode IS that puts ``forward_volts`` across the LED at ``at_amps``."""
    return at_amps / math.exp(forward_volts / (emission * THERMAL_VOLTAGE))


def opamp_subcircuit() -> str:
    """A high-gain op amp whose output stops ``hi`` below V+ and ``lo`` above V-.

    Clipping is the one nonideality a first lab actually sees, so it is the one
    the model keeps. Everything else is ideal.
    """
    return (
        ".subckt railamp inp inn out vp vn params: hi=1.5 lo=0.5\n"
        "E1 oa 0 inp inn 100k\n"
        "Rout oa out 50\n"
        "Vhi vp clamp_hi dc {hi}\n"
        "Dhi out clamp_hi dclamp\n"
        "Vlo clamp_lo vn dc {lo}\n"
        "Dlo clamp_lo out dclamp\n"
        ".model dclamp D(N=0.01)\n"
        ".ends railamp"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_parts.py -q`
Expected: `23 passed`

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/parts.py tests/test_parts.py
git commit -m "Add the part library the breadboard and bench simulator share"
```

---

### Task 2: Build description parsing

**Files:**
- Create: `src/circuit_mcp/breadboard.py` (first section; Tasks 3 and 4 append to it)
- Test: `tests/test_breadboard.py`

**Interfaces:**
- Consumes: `parts.chip`, `parts.parse_value`, `parts.PartError`.
- Produces: `parse_build(content) -> Build`; dataclasses `Build(vplus, vminus, chips, parts, opamps, sources, probes, pot_positions)`, `Part(ref, kind, value, nodes)`, `OpAmpUse(ref, chip, section, inp, inn, out)`, `Source(ref, kind, node, amplitude, freq)`, `Probe(label, node, role)`; `Build.dual_supply`, `Build.nets()`; constants `GROUND`, `COLUMNS`, `CHIP_COL0`, `CHIP_COLS`; `BuildError`.

- [ ] **Step 1: Write the failing tests**

```python
"""Breadboard builds: a description the agent writes only after the netlist is confirmed."""
from __future__ import annotations

import copy

import pytest

from circuit_mcp.breadboard import GROUND, BuildError, parse_build

MINIMAL = {
    "supply": {"vplus": 15, "vminus": -15},
    "chips": [{"ref": "U1", "part": "LM324"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["fb", "0"]},
        {"ref": "R2", "kind": "resistor", "value": "15k", "nodes": ["out", "fb"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "a", "inp": "vi", "inn": "fb", "out": "out"}],
    "sources": [{"ref": "VS", "kind": "sine", "node": "vi", "vrms": 0.5, "freq": 1000}],
    "probes": [{"label": "CH2 vo", "node": "out", "role": "output"}],
}


def spec(**changes):
    result = copy.deepcopy(MINIMAL)
    result.update(changes)
    return result


def test_a_minimal_build_parses_with_ground_collapsed_and_sections_uppercased():
    build = parse_build(MINIMAL)
    assert build.dual_supply is True
    assert build.parts[0].nodes == ("fb", GROUND)
    assert build.opamps[0].section == "A"
    assert build.sources[0].amplitude == 0.5 and build.sources[0].freq == 1000
    assert build.nets() == {"fb", GROUND, "out", "vi"}


@pytest.mark.parametrize("alias", ["0", "gnd", "GND", "ground"])
def test_every_ground_alias_is_the_same_net(alias):
    build = parse_build(spec(parts=[{"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["out", alias]}]))
    assert build.parts[0].nodes[1] == GROUND


def test_single_supply_is_vminus_zero():
    build = parse_build(spec(supply={"vplus": 15, "vminus": 0}))
    assert build.dual_supply is False


@pytest.mark.parametrize("supply,message", [
    (None, "supply object"), ({"vplus": -15, "vminus": 0}, "positive"), ({"vplus": 15, "vminus": 5}, "negative"),
])
def test_bad_supplies_are_refused(supply, message):
    with pytest.raises(BuildError, match=message):
        parse_build(spec(supply=supply))


def test_duplicate_refs_are_refused():
    with pytest.raises(BuildError, match="duplicate ref 'R1'"):
        parse_build(spec(parts=MINIMAL["parts"] + [{"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["a", "b"]}]))


def test_a_pot_needs_three_nodes_and_an_led_two():
    with pytest.raises(BuildError, match="pot needs exactly 3"):
        parse_build(spec(parts=[{"ref": "R3", "kind": "pot", "value": "10k", "nodes": ["a", "b"]}]))
    with pytest.raises(BuildError, match="led needs exactly 2"):
        parse_build(spec(parts=[{"ref": "D1", "kind": "led", "nodes": ["a"]}]))


def test_unknown_kinds_chips_and_sections_are_refused():
    with pytest.raises(BuildError, match="kind must be one of"):
        parse_build(spec(parts=[{"ref": "Q1", "kind": "transistor", "value": "", "nodes": ["a", "b"]}]))
    with pytest.raises(BuildError, match="unknown chip"):
        parse_build(spec(chips=[{"ref": "U1", "part": "NE555"}]))
    with pytest.raises(BuildError, match="no section"):
        parse_build(spec(opamps=[{"ref": "U1A", "chip": "U1", "section": "E", "inp": "vi", "inn": "fb", "out": "out"}]))


def test_the_same_chip_section_cannot_be_used_twice():
    second = {"ref": "U1X", "chip": "U1", "section": "A", "inp": "vi", "inn": "fb", "out": "out"}
    with pytest.raises(BuildError, match="same chip section"):
        parse_build(spec(opamps=MINIMAL["opamps"] + [second]))


def test_one_chip_per_build():
    with pytest.raises(BuildError, match="one chip per build"):
        parse_build(spec(chips=[{"ref": "U1", "part": "LM324"}, {"ref": "U2", "part": "LMC660"}]))


def test_a_source_must_drive_a_node_that_exists_and_is_not_ground():
    with pytest.raises(BuildError, match="not connected"):
        parse_build(spec(sources=[{"ref": "VS", "kind": "sine", "node": "nowhere", "vrms": 1, "freq": 1000}]))
    with pytest.raises(BuildError, match="cannot drive ground"):
        parse_build(spec(sources=[{"ref": "VS", "kind": "dc", "node": "0", "volts": 1}]))


def test_a_probe_must_name_a_node_in_the_build():
    with pytest.raises(BuildError, match="not in the build"):
        parse_build(spec(probes=[{"label": "x", "node": "nowhere"}]))
    with pytest.raises(BuildError, match="role must be"):
        parse_build(spec(probes=[{"label": "x", "node": "out", "role": "sideways"}]))


def test_rail_names_are_reserved():
    with pytest.raises(BuildError, match="reserved for the supply rails"):
        parse_build(spec(parts=[{"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["vplus", "out"]}]))


def test_pot_positions_default_to_the_middle_and_are_bounded():
    pot = {"ref": "R3", "kind": "pot", "value": "10k", "nodes": ["vi", "w", "0"]}
    assert parse_build(spec(parts=MINIMAL["parts"] + [pot])).pot_positions == {"R3": 0.5}
    assert parse_build(spec(parts=MINIMAL["parts"] + [pot], pot_positions={"R3": 0.9})).pot_positions == {"R3": 0.9}
    with pytest.raises(BuildError, match="between 0 and 1"):
        parse_build(spec(parts=MINIMAL["parts"] + [pot], pot_positions={"R3": 1.5}))
    with pytest.raises(BuildError, match="not a pot"):
        parse_build(spec(pot_positions={"R1": 0.5}))


def test_an_empty_build_is_refused():
    with pytest.raises(BuildError, match="nothing to place"):
        parse_build({"supply": {"vplus": 15, "vminus": 0}})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_breadboard.py -q`
Expected: `ModuleNotFoundError: No module named 'circuit_mcp.breadboard'`

- [ ] **Step 3: Write the module's parsing section**

Create `src/circuit_mcp/breadboard.py` with exactly this content:

```python
"""Breadboard layout for a confirmed build: a picture you can wire from.

The layout is deterministic and is verified before it is returned: every net
in the build must come out as exactly one connected group of strips on the
board, and no two nets may touch. A layout that fails that check is refused,
so the drawing can be plain but never wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from typing import Any

from . import parts as P

COLUMNS = 30
TOP_ROWS = "abcde"      # e is nearest the trench
BOTTOM_ROWS = "fghij"   # f is nearest the trench
CHIP_COL0 = 12          # a DIP-14 occupies columns 12..18
CHIP_COLS = range(CHIP_COL0, CHIP_COL0 + 7)
LEFT_POOL = (10, 8, 6, 4, 2)
RIGHT_POOL = (20, 22, 24, 26, 28)
GROUND = "gnd"
GROUND_ALIASES = {"0", "gnd", "ground"}
NET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PART_KINDS = {"resistor": 2, "capacitor": 2, "inductor": 2, "led": 2, "pot": 3}
SOURCE_KINDS = {"dc", "sine", "square"}
MAX_PARTS = 24

Hole = tuple  # ("top"|"bottom", column, row) or ("rail", name, column)


class BuildError(ValueError):
    """The build description is wrong, or no valid layout exists for it."""


@dataclass(frozen=True)
class Part:
    ref: str
    kind: str
    value: str
    nodes: tuple[str, ...]


@dataclass(frozen=True)
class OpAmpUse:
    ref: str
    chip: str
    section: str
    inp: str
    inn: str
    out: str


@dataclass(frozen=True)
class Source:
    ref: str
    kind: str
    node: str
    amplitude: float   # dc: volts, sine: V_rms, square: V_pp
    freq: float


@dataclass(frozen=True)
class Probe:
    label: str
    node: str
    role: str          # "input", "output", or ""


@dataclass(frozen=True)
class Build:
    vplus: float
    vminus: float
    chips: dict[str, P.Chip]
    parts: tuple[Part, ...]
    opamps: tuple[OpAmpUse, ...]
    sources: tuple[Source, ...]
    probes: tuple[Probe, ...]
    pot_positions: dict[str, float]

    @property
    def dual_supply(self) -> bool:
        return self.vminus < 0

    def nets(self) -> set[str]:
        nets = {n for p in self.parts for n in p.nodes}
        nets |= {n for o in self.opamps for n in (o.inp, o.inn, o.out)}
        return nets


# --- parsing -----------------------------------------------------------------

def _node(name: Any, where: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise BuildError(f"{where}: node name must be text")
    name = name.strip()
    if name.lower() in GROUND_ALIASES:
        return GROUND
    if not NET_NAME.match(name):
        raise BuildError(f"{where}: node name {name!r} must be letters, digits, or underscore")
    if name.lower() in ("vplus", "vminus"):
        raise BuildError(f"{where}: {name!r} is reserved for the supply rails")
    return name


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildError(f"{where} must be a number")
    return float(value)


def _ref(value: Any, where: str, seen: set[str]) -> str:
    if not isinstance(value, str) or not re.match(r"^[A-Za-z][A-Za-z0-9]*$", value):
        raise BuildError(f"{where}: ref must be letters and digits, like R1 or U1A")
    if value in seen:
        raise BuildError(f"duplicate ref {value!r}")
    seen.add(value)
    return value


def parse_build(content: Any) -> Build:
    if not isinstance(content, dict):
        raise BuildError("build must be an object")
    supply = content.get("supply")
    if not isinstance(supply, dict):
        raise BuildError("build needs a supply object with vplus and vminus")
    vplus = _number(supply.get("vplus"), "supply.vplus")
    vminus = _number(supply.get("vminus", 0), "supply.vminus")
    if vplus <= 0 or vminus > 0:
        raise BuildError("supply.vplus must be positive and supply.vminus zero or negative")

    seen: set[str] = set()
    chips: dict[str, P.Chip] = {}
    for entry in content.get("chips") or []:
        if not isinstance(entry, dict):
            raise BuildError("each chip must be an object")
        ref = _ref(entry.get("ref"), "chip", seen)
        try:
            chips[ref] = P.chip(str(entry.get("part", "")))
        except P.PartError as exc:
            raise BuildError(str(exc)) from exc
    if len(chips) > 1:
        raise BuildError("one chip per build; a quad op amp has four sections")

    parts: list[Part] = []
    for entry in content.get("parts") or []:
        if not isinstance(entry, dict):
            raise BuildError("each part must be an object")
        ref = _ref(entry.get("ref"), "part", seen)
        kind = str(entry.get("kind", ""))
        if kind not in PART_KINDS:
            raise BuildError(f"{ref}: kind must be one of {', '.join(PART_KINDS)}")
        nodes = entry.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != PART_KINDS[kind]:
            raise BuildError(f"{ref}: a {kind} needs exactly {PART_KINDS[kind]} nodes")
        value = str(entry.get("value", "")).strip()
        if kind != "led":
            try:
                P.parse_value(value)
            except P.PartError as exc:
                raise BuildError(f"{ref}: {exc}") from exc
        parts.append(Part(ref, kind, value, tuple(_node(n, ref) for n in nodes)))
    if len(parts) > MAX_PARTS:
        raise BuildError(f"at most {MAX_PARTS} parts per build")

    opamps: list[OpAmpUse] = []
    for entry in content.get("opamps") or []:
        if not isinstance(entry, dict):
            raise BuildError("each opamp must be an object")
        ref = _ref(entry.get("ref"), "opamp", seen)
        chip_ref = str(entry.get("chip", ""))
        if chip_ref not in chips:
            raise BuildError(f"{ref}: chip {chip_ref!r} is not in the build")
        section = str(entry.get("section", ""))
        try:
            chips[chip_ref].section(section)
        except P.PartError as exc:
            raise BuildError(f"{ref}: {exc}") from exc
        opamps.append(OpAmpUse(ref, chip_ref, section.upper(),
                               _node(entry.get("inp"), f"{ref}.inp"),
                               _node(entry.get("inn"), f"{ref}.inn"),
                               _node(entry.get("out"), f"{ref}.out")))
    used_sections = [(o.chip, o.section) for o in opamps]
    if len(used_sections) != len(set(used_sections)):
        raise BuildError("two opamps use the same chip section")
    if not parts and not opamps:
        raise BuildError("build has nothing to place")

    known = {n for p in parts for n in p.nodes} | {n for o in opamps for n in (o.inp, o.inn, o.out)}

    sources: list[Source] = []
    for entry in content.get("sources") or []:
        if not isinstance(entry, dict):
            raise BuildError("each source must be an object")
        ref = _ref(entry.get("ref"), "source", seen)
        kind = str(entry.get("kind", ""))
        if kind not in SOURCE_KINDS:
            raise BuildError(f"{ref}: source kind must be one of {', '.join(sorted(SOURCE_KINDS))}")
        node = _node(entry.get("node"), ref)
        if node == GROUND:
            raise BuildError(f"{ref}: a source cannot drive ground")
        if node not in known:
            raise BuildError(f"{ref}: node {node!r} is not connected to any part")
        key = {"dc": "volts", "sine": "vrms", "square": "vpp"}[kind]
        amplitude = _number(entry.get(key), f"{ref}.{key}")
        freq = 0.0 if kind == "dc" else _number(entry.get("freq"), f"{ref}.freq")
        if kind != "dc" and freq <= 0:
            raise BuildError(f"{ref}: freq must be positive")
        sources.append(Source(ref, kind, node, amplitude, freq))

    probes: list[Probe] = []
    for entry in content.get("probes") or []:
        if not isinstance(entry, dict):
            raise BuildError("each probe must be an object")
        label = str(entry.get("label", "")).strip()
        if not label or len(label) > 40:
            raise BuildError("probe label must be 1 to 40 characters")
        node = _node(entry.get("node"), f"probe {label}")
        if node not in known:
            raise BuildError(f"probe {label!r}: node {node!r} is not in the build")
        role = str(entry.get("role", ""))
        if role not in ("", "input", "output"):
            raise BuildError(f"probe {label!r}: role must be input, output, or omitted")
        probes.append(Probe(label, node, role))

    positions: dict[str, float] = {}
    for ref, pos in (content.get("pot_positions") or {}).items():
        if ref not in {p.ref for p in parts if p.kind == "pot"}:
            raise BuildError(f"pot_positions: {ref!r} is not a pot in this build")
        value = _number(pos, f"pot_positions.{ref}")
        if not 0 <= value <= 1:
            raise BuildError(f"pot_positions.{ref} must be between 0 and 1")
        positions[ref] = value
    for part in parts:
        if part.kind == "pot":
            positions.setdefault(part.ref, 0.5)

    return Build(vplus, vminus, chips, tuple(parts), tuple(opamps), tuple(sources), tuple(probes), positions)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_breadboard.py -q`
Expected: `19 passed`

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/breadboard.py tests/test_breadboard.py
git commit -m "Parse and validate a breadboard build description"
```

---

### Task 3: Placement and verification, with the Lab 1 circuits as fixtures

**Files:**
- Modify: `src/circuit_mcp/breadboard.py` (append after `parse_build`)
- Create: `tests/fixtures/__init__.py` (empty), `tests/fixtures/lab1.py`
- Test: `tests/test_breadboard.py` (append)

**Interfaces:**
- Consumes: `parse_build`, `Build`, `parts.Chip`.
- Produces: `place(build) -> Layout` with `Layout.homes: dict[net, ("top"|"bottom", col) | ("rail", name)]`, `.chip: {"ref", "part", "col0", "pins": {pin: hole}}`, `.placed: [{"ref","kind","value","nodes","ends": [hole,...]}]`, `.jumpers: [{"net","ends":[hole,hole]}]`, `.attachments: [{"kind","ref","label","net","hole"}]`; `verify(layout)`; `strip_of(hole)`. A hole is `("top"|"bottom", column, row)` or `("rail", name, column)`; rails are `top+`, `top-`, `bot+`, `bot-`.

- [ ] **Step 1: Write the fixtures**

Create `tests/fixtures/__init__.py` empty (and `tests/__init__.py` empty if `import tests` does not already work under pytest), and `tests/fixtures/lab1.py`:

```python
"""The eight Lab 1 circuits as build descriptions. Real coursework, not toys."""

EXP1_NONINVERTING = {
    "supply": {"vplus": 15, "vminus": -15},
    "chips": [{"ref": "U1", "part": "LM324"}],
    "parts": [
        {"ref": "R3", "kind": "pot", "value": "10k", "nodes": ["vsine", "vi", "gnd"]},
        {"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["fb", "gnd"]},
        {"ref": "R2", "kind": "resistor", "value": "15k", "nodes": ["out", "fb"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "vi", "inn": "fb", "out": "out"}],
    "sources": [{"ref": "VS", "kind": "sine", "node": "vsine", "vrms": 1.0, "freq": 1000}],
    "probes": [{"label": "CH1 vi", "node": "vi", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
    "pot_positions": {"R3": 0.5},
}

EXP3_INVERTING = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "2.2k", "nodes": ["vi", "inn"]},
        {"ref": "R2", "kind": "pot", "value": "100k", "nodes": ["inn", "out", "out"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "gnd", "inn": "inn", "out": "out"}],
    "sources": [{"ref": "VS", "kind": "sine", "node": "vi", "vrms": 0.2, "freq": 1000}],
    "probes": [{"label": "CH1 vi", "node": "vi", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
    "pot_positions": {"R2": 0.5},
}

EXP4A_DIVIDER_LED = {
    "supply": {"vplus": 15, "vminus": 0},
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "2.2k", "nodes": ["vdc", "va"]},
        {"ref": "R2", "kind": "resistor", "value": "6.8k", "nodes": ["va", "gnd"]},
        {"ref": "RL", "kind": "resistor", "value": "1k", "nodes": ["va", "vd"]},
        {"ref": "D1", "kind": "led", "nodes": ["vd", "gnd"]},
    ],
    "sources": [{"ref": "VDC", "kind": "dc", "node": "vdc", "volts": 15}],
    "probes": [{"label": "VA", "node": "va", "role": "output"}],
}

EXP4B_BUFFERED = {
    "supply": {"vplus": 15, "vminus": 0},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "2.2k", "nodes": ["vdc", "va"]},
        {"ref": "R2", "kind": "resistor", "value": "6.8k", "nodes": ["va", "gnd"]},
        {"ref": "RL", "kind": "resistor", "value": "1k", "nodes": ["vb", "vd"]},
        {"ref": "D1", "kind": "led", "nodes": ["vd", "gnd"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "va", "inn": "vb", "out": "vb"}],
    "sources": [{"ref": "VDC", "kind": "dc", "node": "vdc", "volts": 15}],
    "probes": [{"label": "VA", "node": "va", "role": "input"}, {"label": "VB", "node": "vb", "role": "output"}],
}

EXP5_INTEGRATOR = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "10k", "nodes": ["vi", "inn"]},
        {"ref": "R2", "kind": "resistor", "value": "470k", "nodes": ["inn", "out"]},
        {"ref": "C1", "kind": "capacitor", "value": "0.1u", "nodes": ["inn", "out"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "gnd", "inn": "inn", "out": "out"}],
    "sources": [{"ref": "VS", "kind": "square", "node": "vi", "vpp": 10, "freq": 500}],
    "probes": [{"label": "CH1 vi", "node": "vi", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
}

EXP6_DIFFERENCE = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["vb", "inn"]},
        {"ref": "R2", "kind": "resistor", "value": "10k", "nodes": ["inn", "out"]},
        {"ref": "R3", "kind": "resistor", "value": "2.2k", "nodes": ["va", "inp"]},
        {"ref": "R4", "kind": "resistor", "value": "22k", "nodes": ["inp", "gnd"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "inp", "inn": "inn", "out": "out"}],
    "sources": [{"ref": "VA", "kind": "sine", "node": "va", "vrms": 0.25, "freq": 1000},
                {"ref": "VB", "kind": "dc", "node": "vb", "volts": 0}],
    "probes": [{"label": "CH1 va", "node": "va", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
}

EXP7_DAC = {
    "supply": {"vplus": 10, "vminus": -10},
    "chips": [{"ref": "U1", "part": "LM324"}],
    "parts": [
        {"ref": "RD2", "kind": "resistor", "value": "2.5k", "nodes": ["d2", "sum"]},
        {"ref": "RD1", "kind": "resistor", "value": "5k", "nodes": ["d1", "sum"]},
        {"ref": "RD0", "kind": "resistor", "value": "10k", "nodes": ["d0", "sum"]},
        {"ref": "RF", "kind": "resistor", "value": "10k", "nodes": ["sum", "out"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "gnd", "inn": "sum", "out": "out"}],
    "sources": [{"ref": "VD2", "kind": "dc", "node": "d2", "volts": 1},
                {"ref": "VD1", "kind": "dc", "node": "d1", "volts": 0},
                {"ref": "VD0", "kind": "dc", "node": "d0", "volts": 1}],
    "probes": [{"label": "vo", "node": "out", "role": "output"}],
}

EXP8_INSTRUMENTATION = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R2a", "kind": "resistor", "value": "10k", "nodes": ["o2", "n2"]},
        {"ref": "R1", "kind": "resistor", "value": "10k", "nodes": ["n2", "n1"]},
        {"ref": "R2b", "kind": "resistor", "value": "10k", "nodes": ["n1", "o1"]},
        {"ref": "R3a", "kind": "resistor", "value": "1k", "nodes": ["o2", "dn"]},
        {"ref": "R4a", "kind": "resistor", "value": "10k", "nodes": ["dn", "out"]},
        {"ref": "R3b", "kind": "resistor", "value": "1k", "nodes": ["o1", "dp"]},
        {"ref": "R4b", "kind": "resistor", "value": "10k", "nodes": ["dp", "gnd"]},
    ],
    "opamps": [
        {"ref": "U1A", "chip": "U1", "section": "A", "inp": "v2", "inn": "n2", "out": "o2"},
        {"ref": "U1B", "chip": "U1", "section": "B", "inp": "v1", "inn": "n1", "out": "o1"},
        {"ref": "U1C", "chip": "U1", "section": "C", "inp": "dp", "inn": "dn", "out": "out"},
    ],
    "sources": [{"ref": "V1", "kind": "sine", "node": "v1", "vrms": 0.15, "freq": 1000},
                {"ref": "V2", "kind": "dc", "node": "v2", "volts": 0}],
    "probes": [{"label": "CH1 v1", "node": "v1", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
}

ALL = {"exp1": EXP1_NONINVERTING, "exp3": EXP3_INVERTING, "exp4a": EXP4A_DIVIDER_LED, "exp4b": EXP4B_BUFFERED,
       "exp5": EXP5_INTEGRATOR, "exp6": EXP6_DIFFERENCE, "exp7": EXP7_DAC, "exp8": EXP8_INSTRUMENTATION}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_breadboard.py`:

```python


# --- placement -----------------------------------------------------------------

from circuit_mcp.breadboard import CHIP_COL0, place, strip_of, verify  # noqa: E402
from tests.fixtures import lab1  # noqa: E402


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_every_lab1_circuit_places_and_verifies(name):
    layout = place(parse_build(lab1.ALL[name]))
    verify(layout)
    assert len(layout.placed) == len(lab1.ALL[name]["parts"])


def test_the_chip_straddles_the_trench_with_pin_one_bottom_left():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    pins = layout.chip["pins"]
    assert pins[1] == ("bottom", CHIP_COL0, "f")
    assert pins[7] == ("bottom", CHIP_COL0 + 6, "f")
    assert pins[8] == ("top", CHIP_COL0 + 6, "e")
    assert pins[14] == ("top", CHIP_COL0, "e")


def test_opamp_nets_live_on_their_pin_strips():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    assert layout.homes["out"] == ("bottom", CHIP_COL0)        # pin 1
    assert layout.homes["fb"] == ("bottom", CHIP_COL0 + 1)     # pin 2
    assert layout.homes["vi"] == ("bottom", CHIP_COL0 + 2)     # pin 3


def test_rails_carry_the_supply_and_ground():
    dual = place(parse_build(lab1.EXP1_NONINVERTING))
    assert dual.homes[GROUND] == ("rail", "bot-")
    assert dual.homes["vplus"] == ("rail", "bot+")
    assert dual.homes["vminus"] == ("rail", "top+")
    single = place(parse_build(lab1.EXP4B_BUFFERED))
    assert "vminus" not in single.homes
    chip = single.build.chips["U1"]
    minus_strip = strip_of(single.chip["pins"][chip.vminus])
    assert any(strip_of(j["ends"][0]) == minus_strip or strip_of(j["ends"][1]) == minus_strip for j in single.jumpers if j["net"] == GROUND)


def test_every_two_terminal_part_touches_both_of_its_nets():
    layout = place(parse_build(lab1.EXP6_DIFFERENCE))
    for part in layout.placed:
        assert len(part["ends"]) == len(part["nodes"])
        for end in part["ends"]:
            assert end in layout.used


def test_verify_refuses_a_short_between_two_nets():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    layout.jumpers.append({"net": "vi", "ends": [("bottom", CHIP_COL0 + 2, "j"), ("bottom", CHIP_COL0 + 1, "j")]})
    with pytest.raises(BuildError, match="shorted"):
        verify(layout)


def test_verify_refuses_a_net_split_in_two():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    layout.jumpers = [j for j in layout.jumpers if j["ends"][0] != ("rail", "top-", 1)]
    with pytest.raises(BuildError, match="split"):
        verify(layout)


def test_the_board_runs_out_of_columns_loudly():
    parts = [{"ref": f"R{i}", "kind": "resistor", "value": "1k", "nodes": [f"a{i}", f"b{i}"]} for i in range(12)]
    with pytest.raises(BuildError, match="out of free columns"):
        place(parse_build({"supply": {"vplus": 5, "vminus": 0}, "parts": parts}))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_breadboard.py -q`
Expected: `ImportError: cannot import name 'place'`

- [ ] **Step 4: Append the placement section**

Append to `src/circuit_mcp/breadboard.py`:

```python
# --- placement ---------------------------------------------------------------

@dataclass
class Layout:
    build: Build
    homes: dict[str, tuple] = field(default_factory=dict)      # net -> ("top"|"bottom", col) | ("rail", name)
    chip: dict | None = None
    placed: list[dict] = field(default_factory=list)
    jumpers: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    used: set = field(default_factory=set)
    spans: dict = field(default_factory=dict)                  # (side, row) -> [(c1, c2)]
    strip_net: dict = field(default_factory=dict)              # strip -> net (for verification)
    left: list[int] = field(default_factory=lambda: list(LEFT_POOL))
    right: list[int] = field(default_factory=lambda: list(RIGHT_POOL))


def strip_of(hole: Hole) -> tuple:
    return (hole[0], hole[1]) if hole[0] != "rail" else ("rail", hole[1])


def _rail_side(name: str) -> str:
    return "top" if name.startswith("top") else "bottom"


def _rows(side: str) -> str:
    return TOP_ROWS if side == "top" else BOTTOM_ROWS


def _free_hole(layout: Layout, strip: tuple) -> Hole:
    """A free hole in a column strip (outermost row first) or a rail. Loud when full."""
    if strip[0] == "rail":
        for col in range(1, COLUMNS + 1):
            hole = ("rail", strip[1], col)
            if hole not in layout.used:
                layout.used.add(hole)
                return hole
        raise BuildError(f"rail {strip[1]} has no free holes")
    side, col = strip
    for row in (TOP_ROWS if side == "top" else BOTTOM_ROWS[::-1]):
        hole = (side, col, row)
        if hole not in layout.used:
            layout.used.add(hole)
            return hole
    raise BuildError(f"column {col} on the {side} strip has no free holes")


def _take_column(layout: Layout, pool: str, count: int = 1) -> list[int]:
    cols = layout.left if pool == "left" else layout.right
    if len(cols) < count:
        raise BuildError("the board is out of free columns; simplify the build")
    if count == 1:
        return [cols.pop(0)]
    # Adjacent columns for a multi-pin part: use the next pool slot and its neighbours.
    base = cols.pop(0)
    step = -1 if pool == "left" else 1
    chosen = [base + step * i for i in range(count)]
    # Drop pool columns the part now covers or sits next to.
    covered = {c for c in chosen} | {c + step for c in chosen}
    cols[:] = [c for c in cols if c not in covered]
    if any(c < 1 or c > COLUMNS or c in CHIP_COLS for c in chosen):
        raise BuildError("no room for a multi-pin part on this side")
    return chosen


def _assign_home(layout: Layout, net: str, home: tuple) -> None:
    layout.homes[net] = home
    layout.strip_net[home if home[0] == "rail" else (home[0], home[1])] = net


def _jumper(layout: Layout, a: tuple, b: tuple, net: str) -> None:
    """Connect two strips. Both ends take a free hole."""
    if a == b:
        return
    ha, hb = _free_hole(layout, a), _free_hole(layout, b)
    layout.jumpers.append({"net": net, "ends": [ha, hb]})


def _place_horizontal(layout: Layout, part: Part, side: str, c1: int, c2: int) -> list[Hole]:
    """Lay a part along one strip between two columns, outermost free row first.

    Rows e and f are reserved for the chip and trench-crossing parts.
    """
    lo, hi = sorted((c1, c2))
    order = TOP_ROWS[:-1] if side == "top" else BOTTOM_ROWS[1:][::-1]   # a b c d / j i h g
    for row in order:
        holes = [(side, lo, row), (side, hi, row)]
        if any(h in layout.used for h in holes):
            continue
        if any(not (hi < s1 or lo > s2) for s1, s2 in layout.spans.get((side, row), [])):
            continue
        layout.used.update(holes)
        layout.spans.setdefault((side, row), []).append((lo, hi))
        return holes if c1 <= c2 else holes[::-1]
    raise BuildError(f"{part.ref}: no free row between columns {lo} and {hi}")


def _place_two_terminal(layout: Layout, part: Part) -> None:
    home_a, home_b = layout.homes[part.nodes[0]], layout.homes[part.nodes[1]]
    kinds = (home_a[0] == "rail", home_b[0] == "rail")
    if kinds == (False, False):
        (side_a, col_a), (side_b, col_b) = home_a, home_b
        if side_a == side_b:
            ends = _place_horizontal(layout, part, side_a, col_a, col_b)
        else:
            # Across the trench at one end's column; the other end then sits on the
            # far strip at that column, one jumper from its home.
            candidates = ((col_a, side_a, side_b, home_b, part.nodes[1]),
                          (col_b, side_b, side_a, home_a, part.nodes[0]))
            for col, near, far, far_home, far_net in candidates:
                top, bottom = ("top", col, "e"), ("bottom", col, "f")
                if col in CHIP_COLS or top in layout.used or bottom in layout.used:
                    continue
                layout.used.update((top, bottom))
                near_hole, far_hole = (top, bottom) if near == "top" else (bottom, top)
                ends = [near_hole, far_hole] if near == side_a else [far_hole, near_hole]
                _jumper(layout, (far, col), far_home, far_net)
                break
            else:
                col = _take_column(layout, "right")[0]
                top, bottom = ("top", col, "e"), ("bottom", col, "f")
                layout.used.update((top, bottom))
                ends = [top, bottom] if side_a == "top" else [bottom, top]
                _jumper(layout, (side_a, col), home_a, part.nodes[0])
                _jumper(layout, (side_b, col), home_b, part.nodes[1])
    elif kinds == (True, True):
        # Both ends on rails: give the part a column and jumper each end to its rail.
        col = _take_column(layout, "right")[0]
        top, bottom = ("top", col, "a"), ("bottom", col, "j")
        layout.used.update((top, bottom))
        ends = [top, bottom]
        _jumper(layout, ("top", col), home_a, part.nodes[0])
        _jumper(layout, ("bottom", col), home_b, part.nodes[1])
    else:
        column_end, rail_end = (0, 1) if not kinds[0] else (1, 0)
        side, col = layout.homes[part.nodes[column_end]]
        rail = layout.homes[part.nodes[rail_end]][1]
        if _rail_side(rail) == side:
            outer = "a" if side == "top" else "j"
            strip_hole, rail_hole = (side, col, outer), ("rail", rail, col)
            if strip_hole in layout.used or rail_hole in layout.used:
                strip_hole = _free_hole(layout, (side, col))
                rail_hole = _free_hole(layout, ("rail", rail))
            else:
                layout.used.update((strip_hole, rail_hole))
            ends = [strip_hole, rail_hole] if column_end == 0 else [rail_hole, strip_hole]
        else:
            # Rail is on the other side: run the part to a fresh column, jumper that to the rail.
            fresh = _take_column(layout, "left" if col <= CHIP_COL0 else "right")[0]
            holes = _place_horizontal(layout, part, side, col, fresh)
            _jumper(layout, (side, fresh), ("rail", rail), part.nodes[rail_end])
            ends = holes if column_end == 0 else holes[::-1]
    layout.placed.append({"ref": part.ref, "kind": part.kind, "value": part.value,
                          "nodes": list(part.nodes), "ends": ends})


def _place_multi_pin(layout: Layout, part: Part) -> None:
    """A pot or an LED sits in adjacent columns; each pin joins its net."""
    homed = [layout.homes.get(n) for n in part.nodes]
    sides = {h[0] for h in homed if h and h[0] != "rail"}
    side = sides.pop() if len(sides) == 1 else "top"
    touches_source = any(n in {s.node for s in layout.build.sources} for n in part.nodes)
    cols = _take_column(layout, "left" if touches_source else "right", len(part.nodes))
    row = "c" if side == "top" else "h"
    ends: list[Hole] = []
    for net, col in zip(part.nodes, cols):
        hole = (side, col, row)
        layout.used.add(hole)
        ends.append(hole)
        if net not in layout.homes:
            _assign_home(layout, net, (side, col))
        else:
            _jumper(layout, (side, col), layout.homes[net], net)
    layout.placed.append({"ref": part.ref, "kind": part.kind, "value": part.value,
                          "nodes": list(part.nodes), "ends": ends})


def place(build: Build) -> Layout:
    layout = Layout(build)
    # Rails: V+ on the bottom red rail, V- on the top red rail, ground on both blue rails.
    _assign_home(layout, GROUND, ("rail", "bot-"))
    layout.strip_net[("rail", "top-")] = GROUND
    _assign_home(layout, "vplus", ("rail", "bot+"))
    if build.dual_supply:
        _assign_home(layout, "vminus", ("rail", "top+"))
    layout.jumpers.append({"net": GROUND, "ends": [("rail", "top-", 1), ("rail", "bot-", 1)]})
    layout.used.update({("rail", "top-", 1), ("rail", "bot-", 1)})

    # The chip straddles the trench: pins 1-7 along row f, 8-14 along row e right to left.
    extra_pins: list[tuple[str, tuple]] = []
    for ref, chip in build.chips.items():
        pins = {}
        for pin in range(1, chip.pins + 1):
            col = CHIP_COL0 + (pin - 1 if pin <= 7 else chip.pins - pin)
            hole = ("bottom", col, "f") if pin <= 7 else ("top", col, "e")
            pins[pin] = hole
            layout.used.add(hole)
        layout.chip = {"ref": ref, "part": chip.part, "col0": CHIP_COL0, "pins": pins}
        for use in build.opamps:
            section = chip.section(use.section)
            for net, pin in ((use.inp, section.inp), (use.inn, section.inn), (use.out, section.out)):
                strip = strip_of(pins[pin])
                if net not in layout.homes:
                    _assign_home(layout, net, strip)
                elif layout.homes[net] != strip:
                    extra_pins.append((net, strip))
        power_minus = "vminus" if build.dual_supply else GROUND
        extra_pins.append(("vplus", strip_of(pins[chip.vplus])))
        extra_pins.append((power_minus, strip_of(pins[chip.vminus])))

    for part in build.parts:
        if part.kind in ("pot", "led"):
            _place_multi_pin(layout, part)
    source_nets = {s.node for s in build.sources}
    for net in sorted(build.nets()):
        if net not in layout.homes:
            pool = "left" if net in source_nets else "right"
            _assign_home(layout, net, ("top", _take_column(layout, pool)[0]))
    for part in build.parts:
        if part.kind not in ("pot", "led"):
            _place_two_terminal(layout, part)
    for net, strip in extra_pins:
        _jumper(layout, strip, layout.homes[net], net)
    for source in build.sources:
        hole = _free_hole(layout, layout.homes[source.node])
        layout.attachments.append({"kind": "source", "ref": source.ref, "label": f"{source.ref} +", "net": source.node, "hole": hole})
    for probe in build.probes:
        hole = _free_hole(layout, layout.homes[probe.node])
        layout.attachments.append({"kind": "probe", "ref": probe.label, "label": probe.label, "net": probe.node, "hole": hole})
    verify(layout)
    return layout


def verify(layout: Layout) -> None:
    """Every net is one connected group of strips, and no two nets touch."""
    parent: dict[tuple, tuple] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    claims: dict[tuple, str] = dict(layout.strip_net)
    if layout.chip:
        chip = layout.build.chips[layout.chip["ref"]]
        for use in layout.build.opamps:
            section = chip.section(use.section)
            for net, pin in ((use.inp, section.inp), (use.inn, section.inn), (use.out, section.out)):
                claims[strip_of(layout.chip["pins"][pin])] = net
        claims[strip_of(layout.chip["pins"][chip.vplus])] = "vplus"
        claims[strip_of(layout.chip["pins"][chip.vminus])] = "vminus" if layout.build.dual_supply else GROUND
    for part in layout.placed:
        for net, hole in zip(part["nodes"], part["ends"]):
            claims.setdefault(strip_of(hole), net)
            if claims[strip_of(hole)] != net:
                raise BuildError(f"layout error: {part['ref']} end for {net} landed on a strip carrying {claims[strip_of(hole)]}")
    for jumper in layout.jumpers:
        union(strip_of(jumper["ends"][0]), strip_of(jumper["ends"][1]))
    for item in layout.attachments:
        claims.setdefault(strip_of(item["hole"]), item["net"])
    component_net: dict[tuple, str] = {}
    for strip, net in claims.items():
        root = find(strip)
        if component_net.setdefault(root, net) != net:
            raise BuildError(f"layout error: nets {component_net[root]} and {net} are shorted")
    roots: dict[str, set] = {}
    for strip, net in claims.items():
        roots.setdefault(net, set()).add(find(strip))
    for net, found in roots.items():
        if len(found) > 1:
            raise BuildError(f"layout error: net {net} is split into {len(found)} groups")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_breadboard.py -q`
Expected: `34 passed`

- [ ] **Step 6: Commit**

```bash
git add src/circuit_mcp/breadboard.py tests/test_breadboard.py tests/fixtures/__init__.py tests/fixtures/lab1.py
git commit -m "Place a build on a breadboard and verify every net before trusting the layout"
```

---

### Task 4: Wire list and SVG

**Files:**
- Modify: `src/circuit_mcp/breadboard.py` (append)
- Test: `tests/test_breadboard.py` (append)

**Interfaces:**
- Consumes: `Layout`, `place`, `parse_build`.
- Produces: `wire_list(layout) -> list[str]`, `svg(layout) -> str`, `hole_name(hole) -> str` (`a13`, `bot- rail (col 3)`), `layout_payload(content) -> {"svg", "wires", "holes": {ref: [names]}, "probes": [{"label","hole","net"}]}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_breadboard.py`:

```python


# --- output --------------------------------------------------------------------

import re  # noqa: E402

from circuit_mcp.breadboard import hole_name, layout_payload, svg, wire_list  # noqa: E402


def test_hole_names_read_like_a_breadboard():
    assert hole_name(("top", 13, "a")) == "a13"
    assert hole_name(("rail", "bot-", 3)) == "bot- rail (col 3)"


def test_the_wire_list_starts_with_power_and_names_every_part_and_probe():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    wires = wire_list(layout)
    assert wires[0].startswith("Power: +15 V to the bottom red rail")
    assert "-15 V to the top red rail" in wires[0]
    assert any(w.startswith("U1 LM324: straddle the trench with pin 1 at f12") for w in wires)
    for ref in ("R1 1kΩ", "R2 15kΩ", "R3 10kΩ pot"):
        assert any(w.startswith(ref) for w in wires), ref
    assert any("CH1 vi" in w and "probe" in w for w in wires)
    assert any("VS +" in w and "Function generator" in w for w in wires)


def test_single_supply_wire_list_has_no_negative_rail():
    wires = wire_list(place(parse_build(lab1.EXP4A_DIVIDER_LED)))
    assert "top red rail" not in wires[0]


def test_svg_escapes_every_label_the_agent_wrote():
    hostile = copy.deepcopy(lab1.EXP1_NONINVERTING)
    hostile["probes"] = [{"label": "<script>x</script>", "node": "out"}]
    picture = svg(place(parse_build(hostile)))
    assert "<script>" not in picture
    assert "&lt;script&gt;" in picture


def test_svg_draws_the_chip_rails_and_probes():
    picture = svg(place(parse_build(lab1.EXP1_NONINVERTING)))
    assert picture.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "U1 LM324" in picture
    assert ">V+<" in picture and ">V-<" in picture and picture.count(">GND<") == 2
    assert "CH2 vo" in picture


def test_layout_payload_has_what_the_card_renders():
    payload = layout_payload(lab1.EXP4B_BUFFERED)
    assert set(payload) == {"svg", "wires", "holes", "probes"}
    assert all(re.match(r"^([a-j]\d+|(top|bot)[+-] rail \(col \d+\))$", h) for holes in payload["holes"].values() for h in holes)
    assert [p["label"] for p in payload["probes"]] == ["VA", "VB"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_breadboard.py -q`
Expected: `ImportError: cannot import name 'hole_name'`

- [ ] **Step 3: Append the output section**

Append to `src/circuit_mcp/breadboard.py`:

```python
# --- output: wire list and SVG ---------------------------------------------------

def hole_name(hole: Hole) -> str:
    if hole[0] == "rail":
        return f"{hole[1]} rail (col {hole[2]})"
    return f"{hole[2]}{hole[1]}"


def wire_list(layout: Layout) -> list[str]:
    b = layout.build
    steps = [f"Power: +{b.vplus:g} V to the bottom red rail (bot+); "
             + (f"{b.vminus:g} V to the top red rail (top+); " if b.dual_supply else "")
             + "supply COM/ground to both blue rails, and jumper the two blue rails together (col 1)."]
    if layout.chip:
        chip = b.chips[layout.chip["ref"]]
        steps.append(f"{layout.chip['ref']} {chip.part}: straddle the trench with pin 1 at f{CHIP_COL0} "
                     f"(notch to the left); pin 4 (V+) and pin 11 (V-) are wired below.")
    for part in layout.placed:
        unit = P.UNITS.get(part["kind"], "")
        label = f"{part['ref']} {part['value']}{unit}".strip()
        if part["kind"] == "pot":
            a, w, c = part["ends"]
            steps.append(f"{label} pot: end at {hole_name(a)}, wiper at {hole_name(w)}, other end at {hole_name(c)}.")
        elif part["kind"] == "led":
            a, k = part["ends"]
            steps.append(f"{part['ref']} LED: anode (long leg) at {hole_name(a)}, cathode (flat side) at {hole_name(k)}.")
        else:
            steps.append(f"{label}: {hole_name(part['ends'][0])} to {hole_name(part['ends'][1])}.")
    for jumper in layout.jumpers:
        steps.append(f"Jumper ({jumper['net']}): {hole_name(jumper['ends'][0])} to {hole_name(jumper['ends'][1])}.")
    for item in layout.attachments:
        who = "Function generator / supply lead" if item["kind"] == "source" else "Scope or meter probe"
        steps.append(f"{who} {item['label']}: {hole_name(item['hole'])} (net {item['net']}); its ground clip to a blue rail.")
    return steps


PITCH = 20
X0, Y0 = 48, 28
ROW_Y = {"rail-top+": 0, "rail-top-": 1, "a": 3, "b": 4, "c": 5, "d": 6, "e": 7, "f": 9, "g": 10, "h": 11, "i": 12, "j": 13, "rail-bot+": 15, "rail-bot-": 16}
COLORS = {"resistor": "#c8a04a", "capacitor": "#4a8fc8", "inductor": "#8a6ac8", "led": "#d9a33a", "pot": "#5aa86a"}


def _xy(hole: Hole) -> tuple[float, float]:
    if hole[0] == "rail":
        return X0 + (hole[2] - 1) * PITCH, Y0 + ROW_Y[f"rail-{hole[1]}"] * PITCH
    return X0 + (hole[1] - 1) * PITCH, Y0 + ROW_Y[hole[2]] * PITCH


def svg(layout: Layout) -> str:
    width, height = X0 * 2 + (COLUMNS - 1) * PITCH, Y0 * 2 + 16 * PITCH
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="breadboard layout">',
           f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" fill="#f2efe6"/>']
    for name, color in (("top+", "#c0392b"), ("top-", "#2c5aa0"), ("bot+", "#c0392b"), ("bot-", "#2c5aa0")):
        y = Y0 + ROW_Y[f"rail-{name}"] * PITCH
        out.append(f'<line x1="{X0 - 14}" y1="{y}" x2="{X0 + (COLUMNS - 1) * PITCH + 14}" y2="{y}" stroke="{color}" stroke-width="1.5" opacity=".7"/>')
        label = {"top+": ("V-" if layout.build.dual_supply else "unused"), "top-": "GND", "bot+": "V+", "bot-": "GND"}[name]
        out.append(f'<text x="{X0 - 40}" y="{y + 4}" font-size="10" font-family="ui-monospace,monospace" fill="{color}">{label}</text>')
    for col in range(1, COLUMNS + 1):
        x = X0 + (col - 1) * PITCH
        out.append(f'<text x="{x}" y="{Y0 + 2.2 * PITCH}" font-size="8" text-anchor="middle" fill="#888">{col}</text>')
        for row in "abcdefghij":
            y = Y0 + ROW_Y[row] * PITCH
            out.append(f'<circle cx="{x}" cy="{y}" r="2.6" fill="#bbb"/>')
        for rail in ("top+", "top-", "bot+", "bot-"):
            y = Y0 + ROW_Y[f"rail-{rail}"] * PITCH
            out.append(f'<circle cx="{x}" cy="{y}" r="2.2" fill="#bbb"/>')
    for row in "abcdefghij":
        out.append(f'<text x="{X0 - 16}" y="{Y0 + ROW_Y[row] * PITCH + 3}" font-size="9" fill="#666">{row}</text>')
    if layout.chip:
        x1 = X0 + (CHIP_COL0 - 1) * PITCH - 8
        y1 = Y0 + ROW_Y["e"] * PITCH - 8
        w = 6 * PITCH + 16
        h = (ROW_Y["f"] - ROW_Y["e"]) * PITCH + 16
        out.append(f'<rect x="{x1}" y="{y1}" width="{w}" height="{h}" rx="3" fill="#2b2b2b"/>')
        out.append(f'<circle cx="{x1 + 8}" cy="{y1 + h - 8}" r="3" fill="#888"/>')
        out.append(f'<text x="{x1 + w / 2}" y="{y1 + h / 2 + 4}" font-size="11" text-anchor="middle" fill="#eee" font-family="ui-monospace,monospace">{escape(layout.chip["ref"])} {escape(layout.chip["part"])}</text>')
        for pin, hole in layout.chip["pins"].items():
            x, y = _xy(hole)
            out.append(f'<text x="{x}" y="{y + (12 if hole[0] == "bottom" else -6)}" font-size="7" text-anchor="middle" fill="#ddd">{pin}</text>')
    for jumper in layout.jumpers:
        (xa, ya), (xb, yb) = _xy(jumper["ends"][0]), _xy(jumper["ends"][1])
        out.append(f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" stroke="#2e8b57" stroke-width="3" stroke-linecap="round" opacity=".85"/>')
    for part in layout.placed:
        color = COLORS[part["kind"]]
        pts = [_xy(h) for h in part["ends"]]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            out.append(f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" stroke="#555" stroke-width="2"/>')
        (xa, ya), (xb, yb) = pts[0], pts[-1]
        mx, my = (xa + xb) / 2, (ya + yb) / 2
        text = f"{part['ref']} {part['value']}".strip()
        out.append(f'<rect x="{mx - 24}" y="{my - 8}" width="48" height="16" rx="4" fill="{color}" stroke="#333"/>')
        out.append(f'<text x="{mx}" y="{my + 4}" font-size="8.5" text-anchor="middle" font-family="ui-monospace,monospace" fill="#111">{escape(text)}</text>')
    for item in layout.attachments:
        x, y = _xy(item["hole"])
        color = "#b0306a" if item["kind"] == "source" else "#1f6fb0"
        out.append(f'<circle cx="{x}" cy="{y}" r="5" fill="none" stroke="{color}" stroke-width="2"/>')
        out.append(f'<text x="{x + 8}" y="{y - 6}" font-size="9" fill="{color}" font-family="ui-monospace,monospace">{escape(item["label"])}</text>')
    out.append("</svg>")
    return "".join(out)


def layout_payload(content: Any) -> dict[str, Any]:
    build = parse_build(content)
    layout = place(build)
    return {"svg": svg(layout), "wires": wire_list(layout),
            "holes": {p["ref"]: [hole_name(h) for h in p["ends"]] for p in layout.placed},
            "probes": [{"label": a["label"], "hole": hole_name(a["hole"]), "net": a["net"]} for a in layout.attachments if a["kind"] == "probe"]}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_breadboard.py -q`
Expected: `40 passed`

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/breadboard.py tests/test_breadboard.py
git commit -m "Draw the breadboard and write the wire list from a verified layout"
```

---

### Task 5: Bench expectations from ngspice

**Files:**
- Create: `src/circuit_mcp/expect.py`
- Test: `tests/test_expect.py`

**Interfaces:**
- Consumes: `breadboard.parse_build`, `breadboard.GROUND`, `parts.*`, `spice.simulate_spice(netlist, analysis, outputs)`, `spice.SpiceError`.
- Produces: `deck(build) -> str`; `expectations(content) -> {"analysis", "readings": [...], "gains": [...], "swing": {"low","high"}, "notes": [...], "deck"}`. A reading is `{"label","node","role","kind":"dc","volts"}` or `{"label","node","role","kind":"ac","vpp","vpk","vrms","mean","clipped","sparkline"}`; a gain is `{"output","input","gain","basis","phase"?}`. `ExpectError`.

- [ ] **Step 1: Write the failing tests**

```python
"""What the bench should read. Numbers are checked against the hand analysis of Lab 1.

ngspice runs for real here, as it does in test_spice.py: the point of these tests is
that the model agrees with the textbook on the circuits students actually build.
"""
from __future__ import annotations

import copy
import math

import pytest

from circuit_mcp.breadboard import parse_build
from circuit_mcp.expect import ExpectError, deck, expectations
from tests.fixtures import lab1


def reading(result, label):
    return next(r for r in result["readings"] if r["label"] == label)


def test_the_deck_for_exp1_is_complete_and_deterministic():
    text = deck(parse_build(lab1.EXP1_NONINVERTING))
    assert ".subckt railamp" in text
    assert "Vplus vplus 0 dc 15" in text and "Vminus vminus 0 dc -15" in text
    assert "RR3a vsine vi 5000" in text and "RR3b vi 0 5000" in text
    assert "RR1 fb 0 1000" in text and "RR2 out fb 15000" in text
    assert "XU1A vi fb out vplus vminus railamp hi=1.5 lo=0.5" in text
    assert "VVS vsine 0 dc 0 SIN(0 1.41421 1000)" in text
    assert text == deck(parse_build(lab1.EXP1_NONINVERTING))


def test_single_supply_ties_the_chip_to_ground_and_models_the_led():
    text = deck(parse_build(lab1.EXP4B_BUFFERED))
    assert "XU1A va vb vb vplus 0 railamp" in text
    assert "DD1 vd 0 led" in text
    assert ".model led D(IS=" in text


def test_a_square_wave_becomes_a_pulse_source():
    text = deck(parse_build(lab1.EXP5_INTEGRATOR))
    assert "VVS vi 0 dc 0 PULSE(-5 5 0 2e-06 2e-06 0.000998 0.002)" in text


def test_exp1_gain_is_sixteen_in_phase_and_unclipped():
    result = expectations(lab1.EXP1_NONINVERTING)
    gain = result["gains"][0]
    assert math.isclose(gain["gain"], 16.0, rel_tol=0.02) and gain["phase"] == "in phase"
    assert reading(result, "CH2 vo")["clipped"] is False
    assert math.isclose(reading(result, "CH1 vi")["vrms"], 0.5, rel_tol=0.02)


def test_exp1_clips_when_the_pot_is_turned_up():
    hot = copy.deepcopy(lab1.EXP1_NONINVERTING)
    hot["pot_positions"] = {"R3": 0.95}
    result = expectations(hot)
    assert reading(result, "CH2 vo")["clipped"] is True
    assert result["swing"] == {"low": -14.5, "high": 13.5}
    assert any("clipping" in note for note in result["notes"])


def test_exp3_inverting_gain_and_phase():
    gain = expectations(lab1.EXP3_INVERTING)["gains"][0]
    assert math.isclose(gain["gain"], 50 / 2.2, rel_tol=0.02) and gain["phase"] == "inverted"


def test_exp4_divider_with_and_without_the_buffer():
    loaded = expectations(lab1.EXP4A_DIVIDER_LED)
    assert math.isclose(reading(loaded, "VA")["volts"], 5.51, abs_tol=0.05)
    buffered = expectations(lab1.EXP4B_BUFFERED)
    assert math.isclose(reading(buffered, "VA")["volts"], 11.33, abs_tol=0.02)
    assert math.isclose(reading(buffered, "VB")["volts"], reading(buffered, "VA")["volts"], abs_tol=0.01)


def test_exp5_integrator_is_a_five_volt_triangle_in_quadrature():
    result = expectations(lab1.EXP5_INTEGRATOR)
    out = reading(result, "CH2 vo")
    assert math.isclose(out["vpp"], 5.0, rel_tol=0.05) and abs(out["mean"]) < 0.1
    assert out["clipped"] is False
    assert result["gains"][0]["phase"].startswith("quadrature")
    assert result["analysis"].startswith("tran 1e-05 0.239 0.235")   # settle = 5 * 470k * 0.1u


def test_exp6_exp7_exp8_match_the_hand_analysis():
    assert math.isclose(expectations(lab1.EXP6_DIFFERENCE)["gains"][0]["gain"], 10.0, rel_tol=0.02)
    assert math.isclose(reading(expectations(lab1.EXP7_DAC), "vo")["volts"], -5.0, rel_tol=0.01)
    in_amp = expectations(lab1.EXP8_INSTRUMENTATION)["gains"][0]
    assert math.isclose(in_amp["gain"], 30.0, rel_tol=0.02) and in_amp["phase"] == "in phase"


def test_readings_carry_a_sparkline_only_for_ac():
    ac = reading(expectations(lab1.EXP1_NONINVERTING), "CH2 vo")
    assert ac["sparkline"].startswith("<svg") and "<polyline" in ac["sparkline"]
    dc = reading(expectations(lab1.EXP7_DAC), "vo")
    assert "sparkline" not in dc


def test_a_build_without_probes_or_sources_is_refused():
    silent = copy.deepcopy(lab1.EXP1_NONINVERTING)
    silent["probes"] = []
    with pytest.raises(ExpectError, match="at least one probe"):
        expectations(silent)
    dark = copy.deepcopy(lab1.EXP1_NONINVERTING)
    dark["sources"] = []
    with pytest.raises(ExpectError, match="at least one source"):
        expectations(dark)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_expect.py -q`
Expected: `ModuleNotFoundError: No module named 'circuit_mcp.expect'`

- [ ] **Step 3: Write the module**

```python
"""What the bench should read, from the same build the breadboard was drawn from.

Numbers come from ngspice with a rail-limited op amp, so clipping shows up
where the real chip clips. Everything else about the op amp is ideal, which is
the right model for a first lab: measured gains land within resistor tolerance
of these, and a big miss means a wiring error, not a modelling one.
"""
from __future__ import annotations

import math
from typing import Any

from . import parts as P
from . import spice
from .breadboard import GROUND, Build, parse_build

SETTLE_PERIODS = 2
CAPTURE_PERIODS = 2
STEPS_PER_PERIOD = 200
SPARK_POINTS = 120
MAX_STEPS = 80_000


class ExpectError(ValueError):
    """The build cannot be simulated, or the simulation did not produce readings."""


def _spice_node(net: str) -> str:
    return "0" if net == GROUND else net


def deck(build: Build) -> str:
    """The ngspice netlist for a build. Deterministic, and returned to the caller."""
    lines = [P.opamp_subcircuit(),
             f"Vplus vplus 0 dc {build.vplus:g}",
             f"Vminus vminus 0 dc {build.vminus:g}"]
    led_is = P.led_saturation_current()
    lines.append(f".model led D(IS={led_is:.3e} N=2)")
    for part in build.parts:
        nodes = [_spice_node(n) for n in part.nodes]
        if part.kind in P.TWO_TERMINAL:
            lines.append(f"{P.TWO_TERMINAL[part.kind]}{part.ref} {nodes[0]} {nodes[1]} {P.parse_value(part.value):g}")
        elif part.kind == "led":
            lines.append(f"D{part.ref} {nodes[0]} {nodes[1]} led")
        elif part.kind == "pot":
            total = P.parse_value(part.value)
            pos = build.pot_positions[part.ref]
            upper, lower = max(1.0, total * (1 - pos)), max(1.0, total * pos)
            lines.append(f"R{part.ref}a {nodes[0]} {nodes[1]} {upper:g}")
            lines.append(f"R{part.ref}b {nodes[1]} {nodes[2]} {lower:g}")
    for use in build.opamps:
        chip = build.chips[use.chip]
        minus = "vminus" if build.dual_supply else "0"
        lines.append(f"X{use.ref} {_spice_node(use.inp)} {_spice_node(use.inn)} {_spice_node(use.out)} vplus {minus} railamp "
                     f"hi={chip.headroom_high:g} lo={chip.headroom_low:g}")
    for source in build.sources:
        node = _spice_node(source.node)
        if source.kind == "dc":
            lines.append(f"V{source.ref} {node} 0 dc {source.amplitude:g}")
        elif source.kind == "sine":
            lines.append(f"V{source.ref} {node} 0 dc 0 SIN(0 {source.amplitude * math.sqrt(2):.6g} {source.freq:g})")
        else:
            half, period = source.amplitude / 2, 1 / source.freq
            edge = period / 1000
            lines.append(f"V{source.ref} {node} 0 dc 0 PULSE({-half:g} {half:g} 0 {edge:.4g} {edge:.4g} {period / 2 - edge:.6g} {period:.6g})")
    return "\n".join(lines)


def _sparkline(samples: list[float], lo: float, hi: float) -> str:
    """A 240x60 polyline of the captured window, scaled to the chip's swing."""
    step = max(1, len(samples) // SPARK_POINTS)
    pts = samples[::step]
    span = (hi - lo) or 1.0
    coords = " ".join(f"{i * 240 / max(1, len(pts) - 1):.1f},{60 - (v - lo) / span * 60:.1f}" for i, v in enumerate(pts))
    zero = 60 - (0 - lo) / span * 60
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 60" width="240" height="60" role="img">'
            f'<line x1="0" y1="{zero:.1f}" x2="240" y2="{zero:.1f}" stroke="#666" stroke-width="0.5"/>'
            f'<polyline points="{coords}" fill="none" stroke="#4aa3df" stroke-width="1.5"/></svg>')


def _settle_time(build: Build, period: float) -> float:
    """Long enough for the slowest RC in the build to reach steady state.

    ngspice's operating point sees each source at its t=0 value, so an
    integrator starts pinned at a rail and needs five of its own time constants
    to come back, not two periods of the input.
    """
    resistances = [P.parse_value(p.value) for p in build.parts if p.kind in ("resistor", "pot")]
    capacitances = [P.parse_value(p.value) for p in build.parts if p.kind == "capacitor"]
    tau = max(resistances, default=0.0) * max(capacitances, default=0.0)
    return max(SETTLE_PERIODS * period, 5 * tau)


def _phase(correlation: float) -> str:
    """What the scope shows: same shape, flipped, or a quarter period apart."""
    if correlation > 0.3:
        return "in phase"
    if correlation < -0.3:
        return "inverted"
    return "quadrature (about 90 degrees)"


def _swing(build: Build) -> tuple[float, float]:
    if not build.chips:
        return build.vminus, build.vplus
    chip = next(iter(build.chips.values()))
    return build.vminus + chip.headroom_low, build.vplus - chip.headroom_high


def expectations(content: Any) -> dict[str, Any]:
    build = parse_build(content)
    if not build.probes:
        raise ExpectError("add at least one probe to say which nodes to read")
    if not build.sources:
        raise ExpectError("add at least one source; nothing drives this build")
    netlist = deck(build)
    ac = [s for s in build.sources if s.kind != "dc"]
    outputs = [f"v({_spice_node(p.node)})" for p in build.probes]
    lo_limit, hi_limit = _swing(build)
    opamp_outputs = {u.out for u in build.opamps}
    try:
        if ac:
            period = 1 / min(s.freq for s in ac)
            settle = _settle_time(build, period)
            stop = settle + CAPTURE_PERIODS * period
            step = max(period / STEPS_PER_PERIOD, stop / MAX_STEPS)
            result = spice.simulate_spice(netlist, f"tran {step:.6g} {stop:.6g} {settle:.6g}", outputs)
        else:
            result = spice.simulate_spice(netlist, "op", outputs)
    except spice.SpiceError as exc:
        raise ExpectError(f"simulation failed: {exc}") from exc
    points = result["points"]
    if not points:
        raise ExpectError("simulation returned no points")
    readings = []
    covariance: dict[tuple[str, str], float] = {}
    if ac:
        for a in build.probes:
            for b in build.probes:
                xa = [float(row[f"v({_spice_node(a.node)})"]) for row in points]
                xb = [float(row[f"v({_spice_node(b.node)})"]) for row in points]
                ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
                num = sum((u - ma) * (v - mb) for u, v in zip(xa, xb))
                den = math.sqrt(sum((u - ma) ** 2 for u in xa) * sum((v - mb) ** 2 for v in xb)) or 1.0
                covariance[(a.node, b.node)] = num / den   # correlation, -1..1
    for probe in build.probes:
        key = f"v({_spice_node(probe.node)})".lower()
        samples = [float(row[key]) for row in points]
        if ac:
            vmax, vmin = max(samples), min(samples)
            mean = sum(samples) / len(samples)
            vrms = math.sqrt(sum((v - mean) ** 2 for v in samples) / len(samples))
            clipped = probe.node in opamp_outputs and (vmax >= hi_limit - 0.05 or vmin <= lo_limit + 0.05)
            readings.append({"label": probe.label, "node": probe.node, "role": probe.role, "kind": "ac",
                             "vpp": round(vmax - vmin, 4), "vpk": round((vmax - vmin) / 2, 4), "vrms": round(vrms, 4),
                             "mean": round(mean, 4), "clipped": clipped,
                             "sparkline": _sparkline(samples, min(lo_limit, vmin), max(hi_limit, vmax))})
        else:
            readings.append({"label": probe.label, "node": probe.node, "role": probe.role, "kind": "dc",
                             "volts": round(samples[0], 4)})
    gains = []
    inputs = [r for r in readings if r["role"] == "input"]
    for out in (r for r in readings if r["role"] == "output"):
        for inp in inputs:
            if ac and inp["vpk"] > 0:
                gains.append({"output": out["label"], "input": inp["label"], "gain": round(out["vpk"] / inp["vpk"], 3),
                              "basis": "peak", "phase": _phase(covariance[(inp["node"], out["node"])])})
            elif not ac and abs(inp["volts"]) > 1e-9:
                gains.append({"output": out["label"], "input": inp["label"], "gain": round(out["volts"] / inp["volts"], 3), "basis": "dc"})
    notes = []
    for r in readings:
        if r.get("clipped"):
            notes.append(f"{r['label']} is clipping: the op amp output cannot go beyond about {lo_limit:g} V to {hi_limit:g} V on this supply.")
    return {"analysis": result["analysis"], "readings": readings, "gains": gains,
            "swing": {"low": lo_limit, "high": hi_limit}, "notes": notes, "deck": netlist}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_expect.py -q`
Expected: `11 passed` (each ngspice run is well under a second)

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/expect.py tests/test_expect.py
git commit -m "Predict the bench readings for a build with a rail-limited op amp in ngspice"
```

---

### Task 6: Two new card kinds

**Files:**
- Modify: `src/circuit_mcp/cards.py` (`KINDS`, imports, two builders, `_BUILDERS`)
- Modify: `src/circuit_mcp/storage.py:34` (`CARD_KINDS`)
- Modify: `src/circuit_mcp/server.py:1640-1653` (`canvas_card_add` docstring)
- Test: `tests/test_cards.py` (update the `KINDS` assertion, add cases)

**Interfaces:**
- Consumes: `breadboard.layout_payload`, `breadboard.BuildError`, `expect.expectations`, `expect.ExpectError`.
- Produces: `build_card("breadboard", title, build) -> {"kind","title","payload": layout_payload}`; `build_card("expected", title, build) -> {"kind","title","payload": expectations}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cards.py`, change the kinds assertion and append:

```python
def test_the_kinds_are_exactly_the_ones_the_canvas_renders():
    assert KINDS == ("formula", "walkthrough", "vocabulary", "breadboard", "expected")


# --- breadboard and expected ------------------------------------------------------

from tests.fixtures import lab1  # noqa: E402


def test_a_breadboard_card_carries_the_drawing_and_the_wire_list():
    card = build_card("breadboard", "Exp 1 build", lab1.EXP1_NONINVERTING)
    assert card["kind"] == "breadboard"
    assert card["payload"]["svg"].startswith("<svg")
    assert card["payload"]["wires"][0].startswith("Power:")
    assert "R1" in card["payload"]["holes"]


def test_an_expected_card_carries_readings_and_gains():
    card = build_card("expected", "Exp 1 bench", lab1.EXP1_NONINVERTING)
    assert card["payload"]["readings"][1]["kind"] == "ac"
    assert card["payload"]["gains"][0]["gain"] > 15


def test_a_bad_build_is_refused_naming_the_field():
    with pytest.raises(CardError, match="supply"):
        build_card("breadboard", "x", {"parts": []})
    with pytest.raises(CardError, match="at least one probe"):
        build_card("expected", "x", {**lab1.EXP7_DAC, "probes": []})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cards.py -q`
Expected: `FAILED ...test_the_kinds_are_exactly...` and `CardError: kind must be one of formula, walkthrough, vocabulary`

- [ ] **Step 3: Add the kinds**

In `src/circuit_mcp/cards.py`, change the imports and constants:

```python
from .breadboard import BuildError, layout_payload
from .expect import ExpectError, expectations
from .parsing import ParseError, parse_as_written, parse_expression
from .steps import check_steps
from .symbols import bind

KINDS = ("formula", "walkthrough", "vocabulary", "breadboard", "expected")
```

Add two builders before `_BUILDERS` and register them:

```python
def _breadboard(content: dict[str, Any]) -> dict[str, Any]:
    """A verified layout. The build is the agent's, drawn only after the student confirmed it."""
    try:
        return layout_payload(content)
    except BuildError as exc:
        raise CardError(f"breadboard refused: {exc}") from exc


def _expected(content: dict[str, Any]) -> dict[str, Any]:
    """What the bench should read, from ngspice, for the same build."""
    try:
        return expectations(content)
    except (BuildError, ExpectError) as exc:
        raise CardError(f"expectation refused: {exc}") from exc


_BUILDERS = {"formula": _formula, "walkthrough": _walkthrough, "vocabulary": _vocabulary,
             "breadboard": _breadboard, "expected": _expected}
```

In `src/circuit_mcp/storage.py` line 34:

```python
CARD_KINDS = ("formula", "walkthrough", "vocabulary", "breadboard", "expected")
```

In `src/circuit_mcp/server.py`, extend the `canvas_card_add` docstring after the `vocabulary` bullet:

```python
    * ``breadboard`` -- a *build*: ``{"supply": {"vplus", "vminus"}, "chips": [{"ref", "part"}],
      "parts": [{"ref", "kind", "value", "nodes"}], "opamps": [{"ref", "chip", "section",
      "inp", "inn", "out"}], "sources": [...], "probes": [{"label", "node", "role"}],
      "pot_positions": {ref: 0..1}}``. Kinds: resistor, capacitor, inductor, led
      (``[anode, cathode]``), pot (``[end, wiper, end]``). Chips: LM324, LMC660.
      Ground is ``gnd``; ``vminus: 0`` is single supply. The card is the wiring
      picture plus a build-order wire list; a layout that does not realise every
      net is refused. Send a build only after the student has confirmed the
      netlist, never from an unconfirmed image.
    * ``expected`` -- the same build; returns what a meter and scope should show at
      each probe (DC volts, or peak/rms, waveform, clipping) plus gain and phase
      between ``input`` and ``output`` probes, from ngspice with a rail-limited
      op amp. Sources: ``dc`` (``volts``), ``sine`` (``vrms``, ``freq``),
      ``square`` (``vpp``, ``freq``).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cards.py tests/test_canvas.py tests/test_storage.py -q`
Expected: all pass (`test_cards.py` gains 3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/circuit_mcp/cards.py src/circuit_mcp/storage.py src/circuit_mcp/server.py tests/test_cards.py
git commit -m "Add breadboard and expected canvas cards"
```

---

### Task 7: Render the cards, keep them across reload, and document the workflow

**Files:**
- Modify: `src/circuit_mcp/static/app.js` (`readCanvas` allowlist, `CARD_KINDS`, `componentTitle`, `preferredWidth`, `cardBody`)
- Modify: `src/circuit_mcp/static/canvas.css` (append)
- Modify: `src/circuit_mcp/static/index.html` (bump `app.js?v=`)
- Modify: `CLAUDE.md` (step 9), `README.md` (canvas cards section)
- Test: `tests/test_canvas.py` (append)

**Interfaces:**
- Consumes: card payloads from Task 6 as delivered by `/api/canvas`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_canvas.py`:

```python
def test_app_js_renders_and_keeps_the_breadboard_and_expected_cards(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        read = app_script.split("function readCanvas()", 1)[1].split("function saveCanvas()", 1)[0]
        for kind in ("breadboard", "expected"):
            assert kind in read, f"readCanvas drops {kind}; layout resets on reload"
            assert f"'{kind}'" in app_script.split("const CARD_KINDS=", 1)[1].split(";", 1)[0]
        assert "card.kind==='breadboard'" in app_script and "card.kind==='expected'" in app_script
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_canvas.py -q -k breadboard_and_expected`
Expected: `AssertionError: readCanvas drops breadboard`

- [ ] **Step 3: Update app.js**

In `readCanvas`, extend the allowlist:

```javascript
['visual','ipad','library','problems','tools','activity','formula','walkthrough','vocabulary','breadboard','expected']
```

Replace the `CARD_KINDS`, `componentTitle` extension, and `preferredWidth` lines:

```javascript
Object.assign(componentTitle,{formula:'formulas',walkthrough:'walkthrough',vocabulary:'vocabulary',breadboard:'breadboard',expected:'what to expect'});
const CARD_KINDS=new Set(['formula','walkthrough','vocabulary','breadboard','expected']);
function preferredWidth(kind){return kind==='ipad'?720:kind==='breadboard'?760:kind==='tools'||kind==='activity'?390:kind==='walkthrough'||kind==='expected'?520:kind==='formula'||kind==='vocabulary'?420:340}
```

In `cardBody`, add two branches before the vocabulary fallthrough (`return title+\`<dl ...`):

```javascript
if(card.kind==='breadboard')return title+`<div class="card-board">${p.svg||''}</div><ol class="card-wires">${(p.wires||[]).map(w=>`<li>${escapeHtml(w)}</li>`).join('')}</ol>`;
if(card.kind==='expected'){const rows=(p.readings||[]).map(r=>r.kind==='dc'?`<tr><td>${escapeHtml(r.label)}</td><td>${r.volts.toFixed(3)} V DC</td><td></td></tr>`:`<tr><td>${escapeHtml(r.label)}</td><td>${r.vpk.toFixed(3)} V pk · ${r.vrms.toFixed(3)} V rms${r.clipped?' · <b class="card-clip">CLIPPING</b>':''}</td><td class="card-spark">${r.sparkline||''}</td></tr>`).join('');const gains=(p.gains||[]).map(g=>`<li>${escapeHtml(g.output)} / ${escapeHtml(g.input)}: gain ${g.gain}${g.phase?`, ${escapeHtml(g.phase)}`:''}</li>`).join('');return title+`<table class="card-readings"><tbody>${rows}</tbody></table>${gains?`<ul class="card-gains">${gains}</ul>`:''}${(p.notes||[]).map(n=>`<p class="card-note">${escapeHtml(n)}</p>`).join('')}<p class="card-verified">ngspice · ${escapeHtml(p.analysis||'')} · output swing ${p.swing?.low} to ${p.swing?.high} V</p>`}
```

Bump the cache-buster in `src/circuit_mcp/static/index.html`: `app.js?v=canvas-19`.

- [ ] **Step 4: Append the CSS**

Append to `src/circuit_mcp/static/canvas.css`:

```css
/* breadboard and expected cards */
.workspace-item[data-kind="breadboard"]{min-height:320px}.card-board{overflow-x:auto;border:1px solid #2a2f2c;background:#f2efe6;border-radius:6px}.card-board svg{display:block;min-width:640px}
.card-wires{margin:12px 0 0;padding-left:22px;color:var(--muted);font-size:13px;line-height:1.5}.card-wires li{margin-bottom:4px}
.card-readings{width:100%;border-collapse:collapse;font-size:13px}.card-readings td{padding:6px 8px;border-bottom:1px solid #2a2f2c;color:var(--ink);vertical-align:middle}.card-readings td:first-child{color:#9aa59f;font:10px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;white-space:nowrap}
.card-spark svg{display:block;background:#111412;border:1px solid #2a2f2c}.card-clip{color:#e07b53}
.card-gains{margin:10px 0 0;padding-left:18px;color:var(--ink);font-size:13px}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_canvas.py tests/test_web.py -q`
Expected: all pass

- [ ] **Step 6: Document the workflow**

In `CLAUDE.md`, append to step 9 after "the server renders the math.":

```markdown
   When the student is about to build a circuit, add a `breadboard` card and an
   `expected` card from the same build description, and only after the netlist
   has been confirmed in step 5. The breadboard card is the wiring picture with a
   build-order wire list; the expected card is what the meter and scope should
   read at each probe. Chips are LM324 and LMC660; say which section and which
   pins. If a measurement is far from the expected card, suspect the wiring
   first, then the pot position, then the model.
```

In `README.md`, extend the "Canvas cards" section with one paragraph:

```markdown
Two more kinds turn a confirmed circuit into a bench session. `breadboard` draws
the build on a 30-column board: the chip across the trench with pin numbers,
each part between named holes, jumpers, the supply rails, and where the
generator and probes connect, plus a wire list in build order. The layout is
verified before it is drawn, so every net comes out as one connected group and
no two nets touch. `expected` runs the same build through ngspice with a
rail-limited op amp and reports what each probe should read: DC volts, or peak
and rms with a waveform, whether the output is clipping, and the gain and phase
between input and output probes. The eight Lab 1 circuits are the test
fixtures for both.
```

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (466 + about 65 new)

```bash
git add src/circuit_mcp/static/app.js src/circuit_mcp/static/canvas.css src/circuit_mcp/static/index.html tests/test_canvas.py CLAUDE.md README.md
git commit -m "Render breadboard and expected cards on the desk"
```

---

## Acceptance check (manual, after Task 7)

1. Start the UI: `.venv/bin/python run_ui.py`.
2. From the MCP server (or a Python shell with `PYTHONPATH=src`), call `canvas_card_add("breadboard", "Exp 1 build", lab1.EXP1_NONINVERTING)` and `canvas_card_add("expected", "Exp 1 bench", lab1.EXP1_NONINVERTING)`.
3. On the desk: the breadboard card shows the LM324 across the trench with pin 1 at f12, R1 between f13 and a rail, the pot in three columns on the left, CH1/CH2 probe rings, and a wire list starting with the power step. The expected card shows CH1 at 0.707 V pk, CH2 at 11.3 V pk, gain 16.0 in phase, no clipping.
4. Re-send the expected card with `pot_positions: {"R3": 0.95}`: CH2 shows CLIPPING with the ±13.5/−14.5 V swing note.
5. Reload the page: both cards stay where they were dragged.

## Not in this plan

- Reading a circuit from an image. The agent already does that in the confirm-transcription flow; the build is written from the confirmed netlist.
- More than one chip, or chips other than the two quad op amps. Add a `Chip` to `parts.CHIPS` with its pin map when a lab needs one.
- Auto-routing prettiness. The layout is correct by verification; if it is hard to read, the fix is a different placement heuristic, not a different check.
