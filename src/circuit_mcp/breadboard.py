"""Breadboard layout for a confirmed build: a picture you can wire from.

The layout is deterministic and is verified before it is returned: every net
in the build must come out as exactly one connected group of strips on the
board, and no two nets may touch. A layout that fails that check is refused,
so the drawing can be plain but never wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import parts as P

COLUMNS = 30
TOP_ROWS = "abcde"      # e is nearest the trench
BOTTOM_ROWS = "fghij"   # f is nearest the trench
CHIP_COL0 = 12          # a DIP-14 occupies columns 12..18
CHIP_COLS = range(CHIP_COL0, CHIP_COL0 + 7)
LEFT_POOL = (10, 8, 6, 4, 2)
RIGHT_POOL = (20, 22, 24, 26, 28)
CHIPLESS_RIGHT_POOL = (12, 14, 16, 18, 20, 22, 24, 26, 28)   # with no chip, the middle is free
GROUND = "gnd"
# The four rails, top to bottom as the board is drawn. Everything that depends
# on rail order derives from this: the collision model below, and the renderer's
# row numbers. Two hand-kept copies drifted apart once already.
RAIL_ORDER = ("top+", "top-", "bot+", "bot-")
# The near rail a lead to the far rail lies over: on each side, the outer rail's
# lead crosses the inner one.
CROSSED_RAIL = {RAIL_ORDER[0]: RAIL_ORDER[1], RAIL_ORDER[3]: RAIL_ORDER[2]}
# A wire between two strips starts one row in, leaving the outer row for leads that
# run straight to a rail and for probes.
JUMPER_ROWS = {"top": "bcdae", "bottom": "ihgjf"}
PART_ROWS = {"top": "bcda", "bottom": "ihgj"}
MAX_SPAN = 4   # columns a bent quarter-watt resistor lead reaches
GROUND_ALIASES = {"0", "gnd", "ground"}
NET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PART_KINDS = {"resistor": 2, "capacitor": 2, "inductor": 2, "led": 2, "pot": 3}
SOURCE_KINDS = {"dc", "sine", "square"}
MAX_PARTS = 24
MAX_PROBES = 12
# A tag longer than this cannot be placed on the board beside what it names:
# at 16 the Lab 1 tags all fit, at 24 two of them overlap.
MAX_PROBE_LABEL = 16
MAX_LED_VALUE = 16
BUILD_FIELDS = {"supply", "chips", "parts", "opamps", "sources", "probes", "pot_positions"}

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

def _entries(content: dict, key: str, what: str) -> list:
    """The list stored under ``key``, checked to be a list of objects.

    Five of these guards were written out by hand and differed only in the noun
    they name, which is five places for the wording to drift.
    """
    raw = content.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BuildError(f"{key} must be a list")
    for entry in raw:
        if not isinstance(entry, dict):
            raise BuildError(f"each {what} must be an object")
    return raw


def _node(name: Any, where: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise BuildError(f"{where}: node name must be text")
    name = name.strip()
    if name.lower() in GROUND_ALIASES:
        return GROUND
    if not NET_NAME.match(name):
        raise BuildError(
            f"{where}: node name {name!r} must start with a letter and continue "
            f"with letters, digits, or underscore")
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
    unknown = set(content) - BUILD_FIELDS
    if unknown:
        # Dropping a key silently drew a build nobody described: "opamp" left the
        # chip unwired, "pot_position" reset every knob to the middle.
        raise BuildError(f"unknown build field(s): {', '.join(sorted(unknown))}")
    supply = content.get("supply")
    if not isinstance(supply, dict):
        raise BuildError("build needs a supply object with vplus and vminus")
    vplus = _number(supply.get("vplus"), "supply.vplus")
    vminus = _number(supply.get("vminus", 0), "supply.vminus")
    if vplus <= 0 or vminus > 0:
        raise BuildError("supply.vplus must be positive and supply.vminus zero or negative")

    seen: set[str] = set()
    chips: dict[str, P.Chip] = {}
    for entry in _entries(content, "chips", "chip"):
        ref = _ref(entry.get("ref"), "chip", seen)
        try:
            chips[ref] = P.chip(str(entry.get("part", "")))
        except P.PartError as exc:
            raise BuildError(str(exc)) from exc
    if len(chips) > 1:
        raise BuildError("one chip per build; a quad op amp has four sections")

    parts: list[Part] = []
    for entry in _entries(content, "parts", "part"):
        ref = _ref(entry.get("ref"), "part", seen)
        kind = str(entry.get("kind", ""))
        if kind not in PART_KINDS:
            raise BuildError(f"{ref}: kind must be one of {', '.join(PART_KINDS)}")
        nodes = entry.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != PART_KINDS[kind]:
            raise BuildError(f"{ref}: a {kind} needs exactly {PART_KINDS[kind]} nodes")
        raw_value = entry.get("value", "")
        if kind == "led":
            # An LED's value is a colour or a part number, so it is not parsed as
            # a quantity -- but it is still drawn, so it has to be short text.
            if not isinstance(raw_value, str):
                raise BuildError(f"{ref}: an LED value must be text, like 'red' or 'HLMP-3750'")
            value = raw_value.strip()
            if len(value) > MAX_LED_VALUE:
                raise BuildError(f"{ref}: an LED value must be {MAX_LED_VALUE} characters or fewer")
        else:
            value = str(raw_value).strip()
            try:
                P.parse_value(value)
            except P.PartError as exc:
                raise BuildError(f"{ref}: {exc}") from exc
        named = tuple(_node(n, ref) for n in nodes)
        if PART_KINDS[kind] == 2 and named[0] == named[1]:
            # Both leads on one net is a short, not a part; the layout would put
            # them in the same hole. A pot may legitimately repeat a node.
            raise BuildError(f"{ref}: both leads are on {named[0]}")
        parts.append(Part(ref, kind, value, named))
    if len(parts) > MAX_PARTS:
        raise BuildError(f"at most {MAX_PARTS} parts per build")

    opamps: list[OpAmpUse] = []
    for entry in _entries(content, "opamps", "opamp"):
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
    for entry in _entries(content, "sources", "source"):
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
    for entry in _entries(content, "probes", "probe"):
        label = str(entry.get("label", "")).strip()
        if not label or len(label) > MAX_PROBE_LABEL:
            raise BuildError(f"probe label must be 1 to {MAX_PROBE_LABEL} characters")
        node = _node(entry.get("node"), f"probe {label}")
        if node not in known:
            raise BuildError(f"probe {label!r}: node {node!r} is not in the build")
        role = str(entry.get("role", ""))
        if role not in ("", "input", "output"):
            raise BuildError(f"probe {label!r}: role must be input, output, or omitted")
        probes.append(Probe(label, node, role))
    if len(probes) > MAX_PROBES:
        raise BuildError(f"at most {MAX_PROBES} probes per build")

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


def _chip_cols(layout: "Layout") -> range:
    """The columns the chip occupies, or none when the build has no chip."""
    return CHIP_COLS if layout.build.chips else range(0)


def strip_of(hole: Hole) -> tuple:
    return (hole[0], hole[1])


def _rail_side(name: str) -> str:
    return "top" if name.startswith("top") else "bottom"


def _free_hole(layout: Layout, strip: tuple, near: int | None = None, rows: str | None = None) -> Hole:
    """A free hole in a column strip (in `rows` order, else outermost first) or a rail. Loud when full.

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
    for row in rows or (TOP_ROWS if side == "top" else BOTTOM_ROWS[::-1]):
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
    if any(c < 1 or c > COLUMNS or c in _chip_cols(layout) for c in chosen):
        raise BuildError("no room for a multi-pin part on this side")
    return chosen


def _assign_home(layout: Layout, net: str, home: tuple) -> None:
    layout.homes[net] = home
    layout.strip_net[home if home[0] == "rail" else (home[0], home[1])] = net


def _home(layout: Layout, net: str, side: str) -> tuple:
    """Where a connection from `side` of the board joins `net`.

    Both blue rails carry ground, so ground joins the one on its own side and the
    wire never crosses the board.
    """
    if net == GROUND:
        return ("rail", "top-" if side == "top" else "bot-")
    return layout.homes[net]


def _jumper(layout: Layout, a: tuple, b: tuple, net: str) -> None:
    """Connect two strips. Both ends take a free hole.

    Between two column strips on one side, both ends take the same row when one is
    free in both, so the wire lies along that row instead of bending around what
    sits between its ends.
    """
    if a == b:
        return
    if a[0] == b[0] and a[0] != "rail":
        shared = [row for row in JUMPER_ROWS[a[0]] if (a[0], a[1], row) not in layout.used and (b[0], b[1], row) not in layout.used]
        if shared:
            ends = [(a[0], a[1], shared[0]), (b[0], b[1], shared[0])]
            layout.used.update(ends)
            layout.jumpers.append({"net": net, "ends": ends})
            return
    near_a = b[1] if b[0] != "rail" else None
    near_b = a[1] if a[0] != "rail" else None
    rows_a = None if b[0] == "rail" else JUMPER_ROWS.get(a[0])
    rows_b = None if a[0] == "rail" else JUMPER_ROWS.get(b[0])
    ha, hb = _free_hole(layout, a, near_a, rows_a), _free_hole(layout, b, near_b, rows_b)
    layout.jumpers.append({"net": net, "ends": [ha, hb]})


def _horizontal_row(layout: Layout, side: str, c1: int, c2: int) -> str | None:
    """The row a part can lie along between two columns, if any.

    Like a wire, it starts one row in, so the outer row stays free for leads that run
    straight to a rail. Rows e and f are reserved for the chip and trench-crossing parts.
    """
    lo, hi = sorted((c1, c2))
    for row in PART_ROWS[side]:
        if any((side, col, row) in layout.used for col in range(lo, hi + 1)):
            continue   # the body would cover an occupied hole, not just its ends
        if any(not (hi < s1 or lo > s2) for s1, s2 in layout.spans.get((side, row), [])):
            continue
        return row
    return None


def _place_horizontal(layout: Layout, part: Part, side: str, c1: int, c2: int) -> list[Hole]:
    """Lay a part along one strip between two columns; the end at `c1` comes back first."""
    lo, hi = sorted((c1, c2))
    row = _horizontal_row(layout, side, lo, hi)
    if row is None:
        raise BuildError(f"{part.ref}: no free row between columns {lo} and {hi}")
    layout.used.update((side, col, row) for col in range(lo, hi + 1))
    layout.spans.setdefault((side, row), []).append((lo, hi))
    return [(side, c1, row), (side, c2, row)]


def _spare(layout: Layout, side: str, col: int) -> bool:
    """A column strip nothing is plugged into and no net owns."""
    rows = TOP_ROWS if side == "top" else BOTTOM_ROWS
    return (1 <= col <= COLUMNS and col not in _chip_cols(layout) and (side, col) not in layout.strip_net
            and not any((side, col, row) in layout.used for row in rows))


def _claim(layout: Layout, side: str, col: int, net: str) -> None:
    layout.strip_net[(side, col)] = net
    for pool in (layout.left, layout.right):
        if col in pool:
            pool.remove(col)


def _route_to_rail(layout: Layout, part: Part, side: str, col: int, net: str) -> list[Hole]:
    """Lay a part from `col` to a spare column within reach, for a wire to the rail's net.

    The column end comes back first. This is the route for a part whose rail is on
    the other side of the board, and for one whose straight run at `col` is blocked.
    """
    reach = [c for c in range(col - MAX_SPAN, col + MAX_SPAN + 1)
             if c != col and _spare(layout, side, c) and _horizontal_row(layout, side, col, c)]
    if reach:
        spare = min(reach, key=lambda c: (abs(abs(c - col) - 2), abs(c - col), c))
    else:
        spare = _take_column(layout, "left" if col <= CHIP_COL0 else "right")[0]
    _claim(layout, side, spare, net)
    return _place_horizontal(layout, part, side, col, spare)


def _lay_between(layout: Layout, part: Part, side: str, col_a: int, col_b: int) -> list[Hole]:
    """Join two columns on one side without stretching the part past MAX_SPAN.

    Too far apart, the part runs from one column to a spare column as near the
    other as it reaches, and the wiring covers the rest.
    """
    if abs(col_a - col_b) <= MAX_SPAN:
        return _place_horizontal(layout, part, side, col_a, col_b)
    options = [(abs(spare - other), -abs(spare - anchor), spare, anchor, net)
               for anchor, other, net in ((col_a, col_b, part.nodes[1]), (col_b, col_a, part.nodes[0]))
               for spare in range(anchor - MAX_SPAN, anchor + MAX_SPAN + 1)
               if spare != anchor and _spare(layout, side, spare) and _horizontal_row(layout, side, anchor, spare)]
    if not options:
        return _place_horizontal(layout, part, side, col_a, col_b)
    _, _, spare, anchor, net = min(options)
    _claim(layout, side, spare, net)
    holes = _place_horizontal(layout, part, side, anchor, spare)
    return holes if anchor == col_a else holes[::-1]


def _record(layout: Layout, part: Part, ends: list[Hole]) -> None:
    """Add a placed part, claiming each strip a lead sits in for that lead's net."""
    for net, hole in zip(part.nodes, ends):
        layout.strip_net.setdefault(strip_of(hole), net)
    layout.placed.append({"ref": part.ref, "kind": part.kind, "value": part.value,
                          "nodes": list(part.nodes), "ends": ends})


def _place_two_terminal(layout: Layout, part: Part) -> None:
    home_a, home_b = layout.homes[part.nodes[0]], layout.homes[part.nodes[1]]
    kinds = (home_a[0] == "rail", home_b[0] == "rail")
    if kinds == (False, False):
        (side_a, col_a), (side_b, col_b) = home_a, home_b
        if side_a == side_b:
            ends = _lay_between(layout, part, side_a, col_a, col_b)
        else:
            # Across the trench at one end's column; the other end then sits on the
            # far strip at that column, one jumper from its home.
            for col, near, far_net in ((col_a, side_a, part.nodes[1]), (col_b, side_b, part.nodes[0])):
                top, bottom = ("top", col, "e"), ("bottom", col, "f")
                far = "bottom" if near == "top" else "top"
                if (col in _chip_cols(layout) or top in layout.used or bottom in layout.used
                        or layout.strip_net.get((far, col), far_net) != far_net):
                    continue   # the far strip already belongs to another net
                layout.used.update((top, bottom))
                near_hole, far_hole = (top, bottom) if near == "top" else (bottom, top)
                ends = [near_hole, far_hole] if near == side_a else [far_hole, near_hole]
                break
            else:
                col = _take_column(layout, "right")[0]
                top, bottom = ("top", col, "e"), ("bottom", col, "f")
                layout.used.update((top, bottom))
                ends = [top, bottom] if side_a == "top" else [bottom, top]
    elif kinds == (True, True):
        # Both ends on rails: give the part a column and jumper each end to its rail.
        col = _take_column(layout, "right")[0]
        top, bottom = ("top", col, "a"), ("bottom", col, "j")
        layout.used.update(("top", col, r) for r in TOP_ROWS)
        layout.used.update(("bottom", col, r) for r in BOTTOM_ROWS)
        ends = [top, bottom]
    else:
        column_end, rail_end = (0, 1) if not kinds[0] else (1, 0)
        side, col = layout.homes[part.nodes[column_end]]
        rail = _home(layout, part.nodes[rail_end], side)[1]
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
            holes = _route_to_rail(layout, part, side, col, part.nodes[rail_end])
            ends = holes if column_end == 0 else holes[::-1]
    _record(layout, part, ends)


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
    span = sorted(cols)
    layout.spans.setdefault((side, row), []).append((span[0], span[-1]))
    _record(layout, part, ends)


def _chip_claims(layout: Layout) -> dict[tuple, str]:
    """The net on each strip a chip pin sits in."""
    if not layout.chip:
        return {}
    chip, pins = layout.build.chips[layout.chip["ref"]], layout.chip["pins"]
    claims = {}
    for use in layout.build.opamps:
        section = chip.section(use.section)
        for net, pin in ((use.inp, section.inp), (use.inn, section.inn), (use.out, section.out)):
            claims[strip_of(pins[pin])] = net
    claims[strip_of(pins[chip.vplus])] = "vplus"
    claims[strip_of(pins[chip.vminus])] = "vminus" if layout.build.dual_supply else GROUND
    return claims


def _wire_cost(a: tuple, b: tuple) -> int:
    """How much wire joining two strips takes, in columns."""
    if a[0] == "rail" and b[0] == "rail":
        return 0   # only ground has two rails, and the bridge already joins them
    if b[0] == "rail":
        a, b = b, a
    if a[0] == "rail":
        return 1 if _rail_side(a[1]) == b[0] else 12
    return abs(a[1] - b[1]) + (0 if a[0] == b[0] else 8)


def _wire_nets(layout: Layout) -> None:
    """Join each net's strips with the least wire: each strip to the nearest one already joined.

    Wiring every strip back to one home fans a net out into long parallel wires
    that cross; a spanning tree chains them instead. Power nets are wired first.
    """
    owners: dict[str, set] = {}
    for strip, net in (*layout.strip_net.items(), *_chip_claims(layout).items()):
        owners.setdefault(net, set()).add(strip)
    for part in layout.placed:
        for net, hole in zip(part["nodes"], part["ends"]):
            owners.setdefault(net, set()).add(strip_of(hole))
    power = [net for net in ("vplus", "vminus", GROUND) if net in owners]
    for net in power + sorted(set(owners) - set(power)):
        strips = sorted(owners[net], key=str)
        start = layout.homes.get(net, strips[0])
        joined, waiting = [start], [strip for strip in strips if strip != start]
        while waiting:
            _, strip, anchor = min(((_wire_cost(s, j), str(s), str(j)), s, j) for s in waiting for j in joined)
            waiting.remove(strip)
            joined.append(strip)
            if strip[0] == "rail" and anchor[0] == "rail":
                continue
            _jumper(layout, *((anchor, strip) if strip[0] == "rail" else (strip, anchor)), net)


def place(build: Build) -> Layout:
    layout = Layout(build)
    if not build.chips:
        layout.right = list(CHIPLESS_RIGHT_POOL)
    # Rails: V+ on the bottom red rail, V- on the top red rail, ground on both blue rails.
    _assign_home(layout, GROUND, ("rail", "bot-"))
    layout.strip_net[("rail", "top-")] = GROUND
    _assign_home(layout, "vplus", ("rail", "bot+"))
    if build.dual_supply:
        _assign_home(layout, "vminus", ("rail", "top+"))
    layout.jumpers.append({"net": GROUND, "ends": [("rail", "top-", 1), ("rail", "bot-", 1)]})
    layout.used.update({("rail", "top-", 1), ("rail", "bot-", 1)})

    # The chip straddles the trench: pins 1-7 along row f, 8-14 along row e right to left.
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
    _wire_nets(layout)
    for source in build.sources:
        hole = _free_hole(layout, layout.homes[source.node])
        layout.attachments.append({"kind": "source", "ref": source.ref, "label": f"{source.ref} +", "net": source.node, "hole": hole})
    for probe in build.probes:
        hole = _free_hole(layout, layout.homes[probe.node])
        layout.attachments.append({"kind": "probe", "ref": probe.label, "label": probe.label, "net": probe.node, "hole": hole})
    verify(layout)
    return layout


def body(part: dict) -> set:
    """Every hole a placed part lies over, not just the two it is wired into.

    A resistor bridging a strip to a rail covers the rows between them, and its
    lead over the near rail covers that too. Anything placed under it cannot be
    reached with the part in the way, so the verifier refuses it rather than
    drawing a board that cannot be built.
    """
    ends = part["ends"]
    rails = [end for end in ends if end[0] == "rail"]
    if len(rails) == 1 and len(ends) == 2:
        rail = rails[0]
        strip = ends[0] if ends[1] == rail else ends[1]
        if rail[2] != strip[1]:
            raise BuildError(
                f"layout error: {part['ref']} runs diagonally from {strip} to {rail}")
        side, column, row = strip
        rows = TOP_ROWS if side == "top" else BOTTOM_ROWS
        covered = rows[:rows.index(row) + 1] if side == "top" else rows[rows.index(row):]
        covered_holes = {(side, column, r) for r in covered} | {rail}
        crossed = CROSSED_RAIL.get(rail[1])
        if crossed is not None:
            covered_holes.add(("rail", crossed, column))
        return covered_holes
    (side_a, column_a, row_a), (side_b, column_b, row_b) = ends[0], ends[-1]
    if side_a == side_b and row_a == row_b:
        return {(side_a, column, row_a) for column in range(min(column_a, column_b), max(column_a, column_b) + 1)}
    if side_a != side_b and column_a == column_b and {row_a, row_b} == {TOP_ROWS[0], BOTTOM_ROWS[-1]}:
        return ({("top", column_a, row) for row in TOP_ROWS}
                | {("bottom", column_a, row) for row in BOTTOM_ROWS})
    return set(ends)


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

    claims: dict[tuple, str] = {**layout.strip_net, **_chip_claims(layout)}
    for part in layout.placed:
        if len(part["ends"]) != len(part["nodes"]):
            raise BuildError(f"layout error: {part['ref']} has {len(part['ends'])} ends for {len(part['nodes'])} nodes")
        if len(set(part["ends"])) != len(part["ends"]):
            raise BuildError(f"layout error: {part['ref']} has two leads in one hole")
        for net, hole in zip(part["nodes"], part["ends"]):
            claims.setdefault(strip_of(hole), net)
            if claims[strip_of(hole)] != net:
                raise BuildError(f"layout error: {part['ref']} end for {net} landed on a strip carrying {claims[strip_of(hole)]}")
    # A hole a part lies over cannot be reached with the part in place, so the
    # board would be undrawable from. A part's own ends are of course exempt.
    bodies = {part["ref"]: (body(part), set(part["ends"])) for part in layout.placed}
    for ref, (covered, ends) in bodies.items():
        for other, (other_covered, _) in bodies.items():
            if other > ref and covered & other_covered:
                raise BuildError(
                    f"layout error: {ref} and {other} lie over the same holes "
                    f"{sorted(covered & other_covered)}")
        for jumper in layout.jumpers:
            for end in jumper["ends"]:
                if end in covered and end not in ends:
                    raise BuildError(f"layout error: the jumper for {jumper['net']} sits under {ref}")
        for item in layout.attachments:
            if item["hole"] in covered and item["hole"] not in ends:
                raise BuildError(f"layout error: {item['label']} sits under {ref}")
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
