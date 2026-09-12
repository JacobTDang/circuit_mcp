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
CROSSED_RAIL = {"top+": "top-", "bot-": "bot+"}   # the near rail a lead to the far rail lies over
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
    raw_chips = content.get("chips")
    if raw_chips is not None and not isinstance(raw_chips, list):
        raise BuildError("chips must be a list")
    for entry in raw_chips or []:
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
    raw_parts = content.get("parts")
    if raw_parts is not None and not isinstance(raw_parts, list):
        raise BuildError("parts must be a list")
    for entry in raw_parts or []:
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
    raw_opamps = content.get("opamps")
    if raw_opamps is not None and not isinstance(raw_opamps, list):
        raise BuildError("opamps must be a list")
    for entry in raw_opamps or []:
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
    raw_sources = content.get("sources")
    if raw_sources is not None and not isinstance(raw_sources, list):
        raise BuildError("sources must be a list")
    for entry in raw_sources or []:
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
    raw_probes = content.get("probes")
    if raw_probes is not None and not isinstance(raw_probes, list):
        raise BuildError("probes must be a list")
    for entry in raw_probes or []:
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
    raw_positions = content.get("pot_positions")
    if raw_positions is not None and not isinstance(raw_positions, dict):
        raise BuildError("pot_positions must be an object")
    for ref, pos in (raw_positions or {}).items():
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
    return (hole[0], hole[1])


def _rail_side(name: str) -> str:
    return "top" if name.startswith("top") else "bottom"


def _free_hole(layout: Layout, strip: tuple, near: int | None = None) -> Hole:
    """A free hole in a column strip (outermost row first) or a rail. Loud when full.

    On a rail, `near` is the column of whatever the hole connects to: the nearest
    free column to it wins, so the jumper is short. Ties go to the lower column.
    """
    if strip[0] == "rail":
        columns = range(1, COLUMNS + 1)
        if near is not None:
            columns = sorted(columns, key=lambda c: (abs(c - near), c))
        for col in columns:
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
    near_a = b[1] if b[0] != "rail" else None
    near_b = a[1] if a[0] != "rail" else None
    ha, hb = _free_hole(layout, a, near_a), _free_hole(layout, b, near_b)
    layout.jumpers.append({"net": net, "ends": [ha, hb]})


def _place_horizontal(layout: Layout, part: Part, side: str, c1: int, c2: int) -> list[Hole]:
    """Lay a part along one strip between two columns, outermost free row first.

    Rows e and f are reserved for the chip and trench-crossing parts.
    """
    lo, hi = sorted((c1, c2))
    order = TOP_ROWS[:-1] if side == "top" else BOTTOM_ROWS[1:][::-1]   # a b c d / j i h g
    for row in order:
        holes = [(side, lo, row), (side, hi, row)]
        if any((side, col, row) in layout.used for col in range(lo, hi + 1)):
            continue   # the body would cover an occupied hole, not just its ends
        if any(not (hi < s1 or lo > s2) for s1, s2 in layout.spans.get((side, row), [])):
            continue
        layout.used.update((side, col, row) for col in range(lo, hi + 1))
        layout.spans.setdefault((side, row), []).append((lo, hi))
        return holes if c1 <= c2 else holes[::-1]
    raise BuildError(f"{part.ref}: no free row between columns {lo} and {hi}")


def _route_to_rail(layout: Layout, part: Part, side: str, col: int, rail: str, net: str) -> list[Hole]:
    """Lay a part from `col` to a fresh column on the nearer side, and jumper that to the rail.

    The column end comes back first. This is the route for a part whose rail is on
    the other side of the board, and for one whose straight run at `col` is blocked.
    """
    fresh = _take_column(layout, "left" if col <= CHIP_COL0 else "right")[0]
    holes = _place_horizontal(layout, part, side, col, fresh)
    _jumper(layout, (side, fresh), ("rail", rail), net)
    return holes


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
        layout.used.update(("top", col, r) for r in TOP_ROWS)
        layout.used.update(("bottom", col, r) for r in BOTTOM_ROWS)
        ends = [top, bottom]
        _jumper(layout, ("top", col), home_a, part.nodes[0])
        _jumper(layout, ("bottom", col), home_b, part.nodes[1])
    else:
        column_end, rail_end = (0, 1) if not kinds[0] else (1, 0)
        side, col = layout.homes[part.nodes[column_end]]
        rail = layout.homes[part.nodes[rail_end]][1]
        straight = None
        if _rail_side(rail) == side:
            # Straight out of the outer row to the rail at the same column. A lead to
            # the far rail also lies over the near rail's hole at that column.
            outer = "a" if side == "top" else "j"
            strip_hole, rail_hole = (side, col, outer), ("rail", rail, col)
            covered = [strip_hole, rail_hole]
            crossed = CROSSED_RAIL.get(rail)
            if crossed is not None:
                covered.append(("rail", crossed, col))
            if not any(hole in layout.used for hole in covered):
                layout.used.update(covered)
                straight = [strip_hole, rail_hole]
        if straight is not None:
            ends = straight if column_end == 0 else straight[::-1]
        else:
            # No straight run: lay the part along the strip to a fresh column and
            # jumper that column to the rail.
            holes = _route_to_rail(layout, part, side, col, rail, part.nodes[rail_end])
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
    for col in cols:
        if (side, col, row) in layout.used:
            raise BuildError(f"{part.ref}: hole {row}{col} on the {side} strip is already occupied")
    ends: list[Hole] = []
    for net, col in zip(part.nodes, cols):
        hole = (side, col, row)
        layout.used.add(hole)
        ends.append(hole)
        if net not in layout.homes:
            _assign_home(layout, net, (side, col))
        else:
            _jumper(layout, (side, col), layout.homes[net], net)
    span = sorted(cols)
    layout.spans.setdefault((side, row), []).append((span[0], span[-1]))
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
        if len(part["ends"]) != len(part["nodes"]):
            raise BuildError(f"layout error: {part['ref']} has {len(part['ends'])} ends for {len(part['nodes'])} nodes")
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


# --- output: wire list and SVG ---------------------------------------------------

def hole_name(hole: Hole) -> str:
    if hole[0] == "rail":
        return f"{hole[1]} rail (col {hole[2]})"
    return f"{hole[2]}{hole[1]}"


def _is_rail_bridge(jumper: dict) -> bool:
    """The jumper that ties the two blue rails together, which the power step already asks for."""
    ends = jumper["ends"]
    return all(end[0] == "rail" for end in ends) and {end[1] for end in ends} == {"top-", "bot-"}


def wire_list(layout: Layout) -> list[str]:
    b = layout.build
    steps = [f"Power: +{b.vplus:g} V to the bottom red rail (bot+); "
             + (f"{b.vminus:g} V to the top red rail (top+); " if b.dual_supply else "")
             + "supply COM/ground to both blue rails, and jumper the two blue rails together (col 1)."]
    if layout.chip:
        chip = b.chips[layout.chip["ref"]]
        steps.append(f"{layout.chip['ref']} {chip.part}: straddle the trench with pin 1 at f{CHIP_COL0} "
                     f"(notch to the left); pin {chip.vplus} (V+) and pin {chip.vminus} (V-) are wired below.")
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
        if _is_rail_bridge(jumper):
            continue   # the power step already says to jumper the two blue rails together
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
            # Just in from the pin's own hole, so every number sits on the dark body.
            out.append(f'<text x="{x}" y="{y + (-8 if hole[0] == "bottom" else 12)}" font-size="7" text-anchor="middle" fill="#eee">{pin}</text>')
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
        text = f"{part['ref']} {part['value']}{P.UNITS.get(part['kind'], '')}".strip()
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
