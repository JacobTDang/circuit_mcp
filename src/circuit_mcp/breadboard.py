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
STRIPS = 2 * COLUMNS    # a five-hole column strip either side of the trench
TOP_ROWS = "abcde"      # e is nearest the trench
BOTTOM_ROWS = "fghij"   # f is nearest the trench
CHIP_WIDTH = 7          # a DIP-14 straddles the trench across seven columns
CHIP_COL0 = 12          # one chip occupies columns 12..18
# Where each chip's pin 1 goes, by how many chips the build has. Two leave five
# columns left of the first, five between them for the parts joining the stages,
# and six right of the second; a third would leave too few for the parts.
CHIP_COL0S = {0: (), 1: (CHIP_COL0,), 2: (6, 18)}
MAX_CHIPS = max(CHIP_COL0S)
# An unused op-amp section is tied off as a unity-gain follower, OUT wired to
# IN-, with IN+ held at ground through this resistor; left floating, its inputs
# pick up noise and it can oscillate or rail, disturbing the sections in use.
# TI's LM324 guideline (SLOA277, 7.2) asks for a linear follower with no input on
# a low-impedance node, and 7.1 forbids wiring any input straight to ground or a
# rail: its input diode conducts when the V- pin rises above ground at power-up,
# so the resistor limits that current, well under the LMC660's 5 mA input limit.
# The same tie suits both chips. Ground is inside both input ranges, which reach
# V- and end 1.5 V (LM324) or 2.3 V (LMC660) below V+ -- the LMC660 is rail to
# rail at its output only -- and place() checks that against the supply. On a
# split supply the follower sits at 0 V, mid-supply and linear. On a single
# supply ground is V-, which both inputs accept, and the output rests at its low
# limit: TI's "soft VOL", stable and drawing slightly less current on the LM324,
# and within the LMC660's rail-to-rail output. A mid-supply divider would keep it
# linear at the cost of two more resistors per section on a student's board. The
# expected card simulates only the build's own sections, since a follower at
# ground draws nothing from them.
TIE_RESISTOR = "10k"
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
# Names are checked with fullmatch: in re.match, $ also matches before a final newline.
NET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
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
    # Refused rather than trimmed, as a ref is: the name the student wrote is the
    # name the board and the simulator use.
    if name.lower() in GROUND_ALIASES:
        return GROUND
    if not NET_NAME.fullmatch(name):
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
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", value):
        raise BuildError(f"{where}: ref must be letters and digits, like R1 or U1A")
    # ngspice folds case: R1 and r1 would be two parts on the board and one
    # device to the simulator.
    twin = next((ref for ref in seen if ref.casefold() == value.casefold()), None)
    if twin == value:
        raise BuildError(f"duplicate ref {value!r}")
    if twin is not None:
        raise BuildError(f"refs {twin!r} and {value!r} differ only in case, which the simulator "
                         f"reads as one name; rename one")
    seen.add(value)
    return value


def _one_output_per_node(opamps: list[OpAmpUse], sources: list[Source]) -> None:
    """Refuse a node that two outputs drive: a source and an op amp output each force its voltage.

    The layout gives every output its own hole on the strip, and the connectivity
    check passes because both name the same net, so without this the card tells
    the student to tie the outputs together.
    """
    drivers: dict[str, list[str]] = {}
    for use in opamps:
        drivers.setdefault(use.out, []).append(f"op amp {use.ref}")
    for source in sources:
        drivers.setdefault(source.node, []).append(f"source {source.ref}")
    for node, names in drivers.items():
        if len(names) > 1:
            listed = f"{', '.join(names[:-1])} and {names[-1]}"
            raise BuildError(
                f"{listed} {'both' if len(names) == 2 else 'all'} drive node {node!r}: outputs "
                f"tied together short into each other even at the same setting, and the current can "
                f"damage a supply, a generator or an op amp; give each output its own node, with a "
                f"resistor between them if they are meant to combine")


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
    if len(chips) > MAX_CHIPS:
        raise BuildError(f"at most {MAX_CHIPS} chips per build: a {COLUMNS}-column board has room for "
                         f"{MAX_CHIPS} DIP-14s and the parts around them")
    # Past the absolute maximum the chip can be destroyed, so neither card is made;
    # between that and the recommended range, supply_notes says so on both.
    total = vplus - vminus
    for ref, chip in chips.items():
        if total > chip.supply_absolute_max:
            low, high = chip.supply_range
            raise BuildError(
                f"{ref} {chip.part}: this supply puts {total:g} V between V+ and V-, past the chip's "
                f"{chip.supply_absolute_max:g} V absolute maximum, which can destroy it; its recommended range "
                f"is {low:g} V to {high:g} V, so lower the supply")

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

    wired = [n for p in parts for n in p.nodes] + [n for o in opamps for n in (o.inp, o.inn, o.out)]
    known = set(wired)
    spellings: dict[str, str] = {}
    for net in wired:
        first = spellings.setdefault(net.casefold(), net)
        if first != net:
            # The board would wire these apart while ngspice, folding case, joins them.
            raise BuildError(f"nodes {first!r} and {net!r} differ only in case, which the simulator "
                             f"reads as one node; spell it one way")

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
    _one_output_per_node(opamps, sources)

    probes: list[Probe] = []
    for entry in _entries(content, "probes", "probe"):
        label = str(entry.get("label", "")).strip()
        if not label or len(label) > MAX_PROBE_LABEL:
            raise BuildError(f"probe label must be 1 to {MAX_PROBE_LABEL} characters")
        # A measured reading finds its probe by label, ignoring case, so two
        # labels that read alike would leave it two places to go.
        twin = next((probe.label for probe in probes if probe.label.casefold() == label.casefold()), None)
        if twin == label:
            raise BuildError(f"probe label {label!r} is used twice; give each probe its own label")
        if twin is not None:
            raise BuildError(f"probe labels {twin!r} and {label!r} differ only in case, and a reading "
                             f"is matched to its probe ignoring case; give each probe its own label")
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


def supply_notes(build: Build) -> list[str]:
    """A note for each chip whose supply is outside its recommended range, which parse_build allows.

    Up to the absolute maximum a build is legal, so it is drawn, but not silently:
    Lab 1 runs the LMC660 at +/-8 V, exactly its 16 V maximum, and a bench supply
    set a little high takes it past. Both cards carry the note, so the student
    reads it before the board is powered.
    """
    total = build.vplus - build.vminus
    notes = []
    for ref, chip in build.chips.items():
        low, high = chip.supply_range
        if total > high:
            limit = chip.supply_absolute_max
            margin = "at" if total >= limit else f"{limit - total:g} V under"
            notes.append(f"{ref} {chip.part} runs on {total:g} V between V+ and V-, above the {high:g} V top of its "
                         f"recommended range and {margin} its {limit:g} V absolute maximum: set the supply with a "
                         f"meter before the chip goes in, since a supply that drifts past {limit:g} V can destroy it.")
        elif total < low:
            notes.append(f"{ref} {chip.part} runs on {total:g} V between V+ and V-, below the {low:g} V bottom of its "
                         f"recommended range, where its datasheet no longer promises its gain or its output swing.")
    return notes


# --- placement ---------------------------------------------------------------

@dataclass
class Layout:
    build: Build
    homes: dict[str, tuple] = field(default_factory=dict)      # net -> ("top"|"bottom", col) | ("rail", name)
    chips: list[dict] = field(default_factory=list)            # ref, part, col0, pins {pin: hole}, nets {pin: net}
    placed: list[dict] = field(default_factory=list)
    jumpers: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    used: set = field(default_factory=set)
    spans: dict = field(default_factory=dict)                  # (side, row) -> [(c1, c2)]
    strip_net: dict = field(default_factory=dict)              # strip -> net (for verification)


def _chip_cols(layout: "Layout") -> set[int]:
    """The columns the chips occupy, none when the build has no chip."""
    return {col0 + i for col0 in CHIP_COL0S[len(layout.build.chips)] for i in range(CHIP_WIDTH)}


def _preferred(layout: "Layout", toward: str) -> list[int]:
    """The columns a new home or part tries first, outward from the chips.

    Sources start left of the first chip and everything else right of each chip,
    every other column, so a part can lie between two homes and the column between
    them stays spare for routing. Without a chip the middle of the board is free.
    """
    col0s = CHIP_COL0S[len(layout.build.chips)]
    if toward == "left":
        return list(range((col0s[0] if col0s else CHIP_COL0) - 2, 0, -2))
    if not col0s:
        return list(range(CHIP_COL0, COLUMNS, 2))
    return [col for col0, after in zip(col0s, (*col0s[1:], COLUMNS + 1))
            for col in range(col0 + CHIP_WIDTH + 1, after - 1, 2)]


def _strips_needed(build: Build) -> int:
    """A floor on the column strips any layout of this build takes.

    Each chip covers seven columns on both sides of the trench, and each net off
    the chips that is not a rail needs a strip of its own: no strip carries two nets.
    """
    on_chips = {net for use in build.opamps for net in (use.inp, use.inn, use.out)}
    return 2 * CHIP_WIDTH * len(build.chips) + len(build.nets() - on_chips - {GROUND})


def _no_room(layout: "Layout", what: str) -> BuildError:
    """The search ran out of places to try, on a board that may still have room.

    Worded unlike a full board, so a student is not told to simplify a build that
    fits; the numbers say how close to full the board is.
    """
    free = sum(_spare(layout, side, col) for side in ("top", "bottom") for col in range(1, COLUMNS + 1))
    return BuildError(
        f"the placer could not find room for {what}. That is a limit of the layout search, not proof "
        f"the board is full: this build needs at least {_strips_needed(layout.build)} of the board's "
        f"{STRIPS} column strips, and {free} are still free")


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


def _columns(layout: Layout, toward: str, near: tuple[int, ...] = ()) -> list[int]:
    """Every column on the board, in the order to try it.

    The preferred columns come first; past them, the nearest to `near` (the
    columns of whatever the new piece connects to), so a long build grows from
    its own end instead of from wherever a list of columns left off.
    """
    first = _preferred(layout, toward)
    anchor = near or tuple(first[:1])
    rest = sorted(set(range(1, COLUMNS + 1)) - set(first), key=lambda c: (sum(abs(c - n) for n in anchor), c))
    return first + rest


def _take_run(layout: Layout, side: str, toward: str, count: int, near: tuple[int, ...] = ()) -> list[int] | None:
    """`count` neighbouring spare columns on one side, running away from the chip, or None."""
    step = -1 if toward == "left" else 1
    for base in _columns(layout, toward, near):
        run = [base + step * i for i in range(count)]
        if all(_spare(layout, side, col) for col in run):
            return run
    return None


def _take_through(layout: Layout, near: tuple[int, ...] = ()) -> int | None:
    """A column spare on both sides of the trench, for a part that crosses it there, or None."""
    for col in _columns(layout, "right", near):
        if _spare(layout, "top", col) and _spare(layout, "bottom", col):
            return col
    return None


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


def _route_to_rail(layout: Layout, part: Part, side: str, col: int, net: str) -> list[Hole] | None:
    """Lay a part from `col` to a spare column within reach, for a wire to the rail's net, or None.

    The column end comes back first. This is the route for a part whose rail is on
    the other side of the board, and for one whose straight run at `col` is blocked.
    """
    reach = [c for c in range(col - MAX_SPAN, col + MAX_SPAN + 1)
             if c != col and _spare(layout, side, c) and _horizontal_row(layout, side, col, c)]
    if not reach:
        return None
    spare = min(reach, key=lambda c: (abs(abs(c - col) - 2), abs(c - col), c))
    _claim(layout, side, spare, net)
    return _place_horizontal(layout, part, side, col, spare)


def _lay_between(layout: Layout, part: Part, side: str, col_a: int, col_b: int) -> list[Hole]:
    """Join two columns on one side without stretching the part past MAX_SPAN.

    Too far apart, or with no free row between them, the part runs from one column
    to a spare column as near the other as it reaches, and the wiring covers the
    rest; with no spare in reach either, it is placed apart.
    """
    if abs(col_a - col_b) <= MAX_SPAN and _horizontal_row(layout, side, col_a, col_b):
        return _place_horizontal(layout, part, side, col_a, col_b)
    options = [(abs(spare - other), -abs(spare - anchor), spare, anchor, net)
               for anchor, other, net in ((col_a, col_b, part.nodes[1]), (col_b, col_a, part.nodes[0]))
               for spare in range(anchor - MAX_SPAN, anchor + MAX_SPAN + 1)
               if spare != anchor and _spare(layout, side, spare) and _horizontal_row(layout, side, anchor, spare)]
    if not options:
        return _place_apart(layout, part)
    _, _, spare, anchor, net = min(options)
    _claim(layout, side, spare, net)
    holes = _place_horizontal(layout, part, side, anchor, spare)
    return holes if anchor == col_a else holes[::-1]


def _place_apart(layout: Layout, part: Part) -> list[Hole]:
    """Lay a two-lead part between two spare strips of its own, and let the wiring reach its nets.

    The last resort when nothing next to its nets is free: all it needs is two
    spare strips a lead apart anywhere on the board, the pair taking the least
    wire to the nets' homes.
    """
    options = []
    for side in ("top", "bottom"):
        homes = [_home(layout, net, side) for net in part.nodes]
        spares = [col for col in range(1, COLUMNS + 1) if _spare(layout, side, col)]
        for c1 in spares:
            for c2 in spares:
                if 0 < abs(c1 - c2) <= MAX_SPAN and _horizontal_row(layout, side, c1, c2):
                    cost = _wire_cost((side, c1), homes[0]) + _wire_cost((side, c2), homes[1])
                    options.append((cost, abs(abs(c1 - c2) - 2), side != "top", c1, c2, side))
    if not options:
        raise _no_room(layout, part.ref)
    *_, c1, c2, side = min(options)
    _claim(layout, side, c1, part.nodes[0])
    _claim(layout, side, c2, part.nodes[1])
    return _place_horizontal(layout, part, side, c1, c2)


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
                col = _take_through(layout, near=(col_a, col_b))
                if col is None:
                    ends = _place_apart(layout, part)
                else:
                    top, bottom = ("top", col, "e"), ("bottom", col, "f")
                    layout.used.update((top, bottom))
                    ends = [top, bottom] if side_a == "top" else [bottom, top]
    elif kinds == (True, True):
        # Both ends on rails: give the part a column and jumper each end to its rail.
        col = _take_through(layout)
        if col is None:
            ends = _place_apart(layout, part)
        else:
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
            if holes is None:
                ends = _place_apart(layout, part)
            else:
                ends = holes if column_end == 0 else holes[::-1]
    _record(layout, part, ends)


def _place_multi_pin(layout: Layout, part: Part) -> None:
    """A pot or an LED sits in adjacent spare columns; each pin joins its net."""
    homed = [layout.homes.get(n) for n in part.nodes]
    sides = {h[0] for h in homed if h and h[0] != "rail"}
    first = sides.pop() if len(sides) == 1 else "top"
    touches_source = any(n in {s.node for s in layout.build.sources} for n in part.nodes)
    toward = "left" if touches_source else "right"
    near = tuple(h[1] for h in homed if h and h[0] != "rail")
    for side in (first, "bottom" if first == "top" else "top"):
        cols = _take_run(layout, side, toward, len(part.nodes), near)
        if cols is not None:
            break
    else:
        raise _no_room(layout, part.ref)
    row = "c" if side == "top" else "h"
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


def _unused_sections(build: Build, ref: str) -> list[P.OpAmpSection]:
    """The sections of chip `ref` that no opamp in the build uses."""
    used = {use.section for use in build.opamps if use.chip == ref}
    return [section for section in build.chips[ref].sections if section.name not in used]


def _chip_claims(layout: Layout) -> dict[tuple, str]:
    """The net on each strip a chip pin sits in."""
    return {strip_of(chip["pins"][pin]): net for chip in layout.chips for pin, net in chip["nets"].items()}


def _wire_cost(a: tuple, b: tuple) -> int:
    """How much wire joining two strips takes, in columns."""
    if a[0] == "rail" and b[0] == "rail":
        return 0   # only ground has two rails, and the bridge already joins them
    if b[0] == "rail":
        a, b = b, a
    if a[0] == "rail":
        return 1 if _rail_side(a[1]) == b[0] else 12
    return abs(a[1] - b[1]) + (0 if a[0] == b[0] else 8)


def _net_strips(layout: Layout) -> dict[str, set]:
    """Every strip each net occupies so far."""
    owners: dict[str, set] = {}
    for strip, net in (*layout.strip_net.items(), *_chip_claims(layout).items()):
        owners.setdefault(net, set()).add(strip)
    for part in layout.placed:
        for net, hole in zip(part["nodes"], part["ends"]):
            owners.setdefault(net, set()).add(strip_of(hole))
    return owners


def _has_room(layout: Layout, strip: tuple) -> bool:
    """Whether a wire or a clip can still go into a strip."""
    if strip[0] == "rail":
        return any(("rail", strip[1], col) not in layout.used for col in range(1, COLUMNS + 1))
    side, col = strip
    return any((side, col, row) not in layout.used for row in (TOP_ROWS if side == "top" else BOTTOM_ROWS))


def _wire_nets(layout: Layout) -> None:
    """Join each net's strips with the least wire: each strip to the nearest one already joined.

    Wiring every strip back to one home fans a net out into long parallel wires
    that cross; a spanning tree chains them instead. Power nets are wired first.
    A strip whose five holes are all taken is joined through one that has room.
    """
    owners = _net_strips(layout)
    power = [net for net in ("vplus", "vminus", GROUND) if net in owners]
    # The build's nets before the tie-offs, so its wires keep their place in the list.
    others = sorted(set(owners) - set(power), key=lambda net: (net not in layout.build.nets(), net))
    for net in power + others:
        strips = sorted(owners[net], key=str)
        start = layout.homes.get(net, strips[0])
        joined, waiting = [start], [strip for strip in strips if strip != start]
        while waiting:
            pairs = [((_wire_cost(s, j), str(s), str(j)), s, j) for s in waiting for j in joined
                     if (s[0] == "rail" and j[0] == "rail") or (_has_room(layout, s) and _has_room(layout, j))]
            if not pairs:
                raise _no_room(layout, f"a wire on net {net}")
            _, strip, anchor = min(pairs)
            waiting.remove(strip)
            joined.append(strip)
            if strip[0] == "rail" and anchor[0] == "rail":
                continue
            _jumper(layout, *((anchor, strip) if strip[0] == "rail" else (strip, anchor)), net)


def _clip_hole(layout: Layout, net: str, what: str) -> Hole:
    """A hole for a probe or a generator lead: in the net's home strip, else its nearest strip with room."""
    home = layout.homes[net]
    roomy = [strip for strip in _net_strips(layout)[net] if _has_room(layout, strip)]
    if not roomy:
        raise _no_room(layout, what)
    return _free_hole(layout, min(roomy, key=lambda strip: (strip != home, _wire_cost(strip, home), str(strip))))


def _links(build: Build) -> dict[str, set[str]]:
    """The nets each net shares a part with."""
    links: dict[str, set[str]] = {}
    for part in build.parts:
        for net in part.nodes:
            links.setdefault(net, set()).update(n for n in part.nodes if n != net)
    return links


def _home_nets(layout: Layout) -> None:
    """Give every net that no pin or part has placed yet a strip of its own.

    Nets are placed in rings outward from what is already on the board, each
    beside the nets it shares a part with, so a chain of parts gets neighbouring
    columns: alphabetical order put n10 between n1 and n2. A group that nothing on
    the board touches starts at its source, where its signal starts.
    """
    build = layout.build
    links, sources = _links(build), {s.node for s in build.sources}
    placed = {net for net, home in layout.homes.items() if home[0] != "rail"}
    waiting = build.nets() - set(layout.homes)
    while waiting:
        level = sorted(net for net in waiting if links.get(net, set()) & placed)
        if not level:
            level = [min(waiting, key=lambda net: (net not in sources, net))]
        for net in level:
            near = tuple(layout.homes[n][1] for n in sorted(links.get(net, set()))
                         if n in layout.homes and layout.homes[n][0] != "rail")
            toward = "left" if net in sources else "right"
            for side in ("top", "bottom"):
                cols = _take_run(layout, side, toward, 1, near)
                if cols is not None:
                    _assign_home(layout, net, (side, cols[0]))
                    break
            else:
                raise _no_room(layout, f"net {net}")
        placed.update(level)
        waiting.difference_update(level)


def place(build: Build) -> Layout:
    need = _strips_needed(build)
    if need > STRIPS:
        raise BuildError(f"the board is full: this build needs at least {need} column strips and a "
                         f"{COLUMNS}-column board has {STRIPS}, so it cannot be built on one breadboard")
    layout = Layout(build)
    # Rails: V+ on the bottom red rail, V- on the top red rail, ground on both blue rails.
    _assign_home(layout, GROUND, ("rail", "bot-"))
    layout.strip_net[("rail", "top-")] = GROUND
    _assign_home(layout, "vplus", ("rail", "bot+"))
    if build.dual_supply:
        _assign_home(layout, "vminus", ("rail", "top+"))
    layout.jumpers.append({"net": GROUND, "ends": [("rail", "top-", 1), ("rail", "bot-", 1)]})
    layout.used.update({("rail", "top-", 1), ("rail", "bot-", 1)})

    # Each chip straddles the trench: pins 1-7 along row f, 8-14 along row e right to left.
    for col0, (ref, chip) in zip(CHIP_COL0S[len(build.chips)], build.chips.items()):
        pins = {}
        for pin in range(1, chip.pins + 1):
            col = col0 + (pin - 1 if pin <= 7 else chip.pins - pin)
            hole = ("bottom", col, "f") if pin <= 7 else ("top", col, "e")
            pins[pin] = hole
            layout.used.add(hole)
        nets = {chip.vplus: "vplus", chip.vminus: "vminus" if build.dual_supply else GROUND}
        for use in build.opamps:
            if use.chip == ref:
                section = chip.section(use.section)
                nets.update({section.inp: use.inp, section.inn: use.inn, section.out: use.out})
        tied = _unused_sections(build, ref)
        if tied and build.vplus < chip.input_headroom_high:
            raise BuildError(
                f"{ref} {chip.part}: ground is outside its input common-mode range on a +{build.vplus:g} V supply "
                f"(the range ends {chip.input_headroom_high:g} V below V+), so its unused sections cannot be "
                f"tied off at ground; raise supply.vplus")
        for section in tied:
            # The hyphen keeps these names apart from any net a build can name.
            name = f"{ref}{section.name}"
            nets.update({section.inp: f"{name}-in", section.inn: f"{name}-out", section.out: f"{name}-out"})
        layout.chips.append({"ref": ref, "part": chip.part, "col0": col0, "pins": pins, "nets": nets,
                             "tied": [section.name for section in tied]})
        for pin, net in nets.items():
            if net not in layout.homes:
                _assign_home(layout, net, strip_of(pins[pin]))

    for part in build.parts:
        if part.kind in ("pot", "led"):
            _place_multi_pin(layout, part)
    _home_nets(layout)
    for part in build.parts:
        if part.kind not in ("pot", "led"):
            _place_two_terminal(layout, part)
    # The build's own parts choose their holes first; a tie-off only needs its IN+
    # strip's outer hole and the ground rail beside it.
    for chip in layout.chips:
        for section in chip["tied"]:
            name = f"{chip['ref']}{section}"
            _place_two_terminal(layout, Part(f"{name}-tie", "resistor", TIE_RESISTOR, (f"{name}-in", GROUND)))
    _wire_nets(layout)
    for source in build.sources:
        hole = _clip_hole(layout, source.node, f"the {source.ref} lead")
        layout.attachments.append({"kind": "source", "ref": source.ref, "label": f"{source.ref} +", "net": source.node, "hole": hole})
    for probe in build.probes:
        hole = _clip_hole(layout, probe.node, f"probe {probe.label}")
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

    # Every pin is wired, and a section the build does not use is a follower with
    # IN+ held at ground through a part, never on it directly: the connectivity
    # check below then proves each of those wires is really on the board.
    for chip in layout.chips:
        nets = chip["nets"]
        for pin in range(1, layout.build.chips[chip["ref"]].pins + 1):
            if pin not in nets:
                raise BuildError(f"layout error: {chip['ref']} pin {pin} is left floating")
        for section in _unused_sections(layout.build, chip["ref"]):
            inp = nets[section.inp]
            held = inp != GROUND and any(set(part["nodes"]) == {inp, GROUND} for part in layout.placed)
            if nets[section.out] != nets[section.inn] or not held:
                raise BuildError(f"layout error: unused section {chip['ref']}{section.name} is not tied off as a "
                                 f"follower with IN+ held at ground through a resistor")

    claims: dict[tuple, str] = {**layout.strip_net, **_chip_claims(layout)}
    for part in layout.placed:
        if len(part["ends"]) != len(part["nodes"]):
            raise BuildError(f"layout error: {part['ref']} has {len(part['ends'])} ends for {len(part['nodes'])} nodes")
        if len(set(part["ends"])) != len(part["ends"]):
            raise BuildError(f"layout error: {part['ref']} has two leads in one hole")
        cols = [end[2] if end[0] == "rail" else end[1] for end in part["ends"]]
        if max(cols) - min(cols) > MAX_SPAN:
            raise BuildError(f"layout error: {part['ref']} is stretched {max(cols) - min(cols)} columns; "
                             f"a bent lead reaches {MAX_SPAN}")
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
