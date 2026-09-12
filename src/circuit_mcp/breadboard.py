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
