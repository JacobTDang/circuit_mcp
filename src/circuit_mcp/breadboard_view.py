"""What a student sees: the wire list, the net legend, and the breadboard drawing.

``breadboard.place`` decides where every lead goes and proves each connection;
this module only shows the result. The drawing is organised around what a real
breadboard hides, which holes the metal strips underneath join together: every
strip in use is tinted in its net's colour, every wire wears that colour, the
legend says what each net joins, and every part, wire, and probe carries the
number of the wire-list step that places it, so a step and its picture can be
lit together on the desk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from html import escape
from typing import Any

from . import parts as P
from .breadboard import COLUMNS, GROUND, RAIL_ORDER, Layout, parse_build, place, strip_of

Hole = tuple
Box = tuple    # (x0, y0, x1, y1)
Point = tuple  # (x, y)

# --- colours -------------------------------------------------------------------

POWER_COLOURS = {"vplus": ("#D23A30", "red"), "vminus": ("#2F66C9", "blue"), GROUND: ("#23272B", "black")}
# Jumper-kit colours for signal nets, ordered so neighbours in the list look least
# alike. Red, blue, and black stay reserved for power, so a wire's colour alone says
# whether it is supply, ground, or signal.
SIGNAL_COLOURS = (
    ("#E2730B", "orange"), ("#7E4FC0", "violet"), ("#0F8A8A", "teal"), ("#C93E88", "pink"),
    ("#7A8712", "olive"), ("#1597C4", "cyan"), ("#8C5A2B", "brown"), ("#2F9A45", "green"),
    ("#C69400", "gold"), ("#922B45", "maroon"), ("#58626F", "slate"),
)
# A two-channel scope draws CH1 yellow and CH2 green; four-channel scopes add blue and magenta.
PROBE_COLOURS = ("#C99700", "#1F9A5A", "#2F6FD6", "#C2338F")
INK = "#23272B"
BOARD = "#ECEEE9"
TRENCH = "#DDE0D9"
SILK = "#7C8079"
HOLE_FILL = "#B5B9B1"
LEAD = "#8F959B"
NET_NAMES = {"vplus": "V+", "vminus": "V-", GROUND: "GND"}
PIN_ROLES = (("out", "OUT"), ("inn", "IN-"), ("inp", "IN+"))


@dataclass(frozen=True)
class NetColour:
    hex: str
    name: str
    striped: bool = False


def net_name(net: str) -> str:
    return NET_NAMES.get(net, net)


def hole_name(hole: Hole) -> str:
    if hole[0] == "rail":
        return f"{hole[1]} rail (col {hole[2]})"
    return f"{hole[2]}{hole[1]}"


def is_rail_bridge(jumper: dict) -> bool:
    """The wire tying the two blue rails together, which the power step already asks for."""
    ends = jumper["ends"]
    return all(end[0] == "rail" for end in ends) and {end[1] for end in ends} == {"top-", "bot-"}


def _signal_nets(layout: Layout) -> list[str]:
    """Signal nets in the order a student meets them: op-amp pins, then parts, then probes."""
    candidates = [net for use in layout.build.opamps for net in (use.out, use.inn, use.inp)]
    candidates += [net for part in layout.placed for net in part["nodes"]]
    candidates += [item["net"] for item in layout.attachments]
    order: list[str] = []
    for net in candidates:
        if net not in POWER_COLOURS and net not in order:
            order.append(net)
    return order


def net_colours(layout: Layout) -> dict[str, NetColour]:
    """One colour per net. Past the palette a colour repeats with a white stripe, so a repeat still reads as a different net."""
    colours = {"vplus": NetColour(*POWER_COLOURS["vplus"])}
    if layout.build.dual_supply:
        colours["vminus"] = NetColour(*POWER_COLOURS["vminus"])
    colours[GROUND] = NetColour(*POWER_COLOURS[GROUND])
    for index, net in enumerate(_signal_nets(layout)):
        hex_, name = SIGNAL_COLOURS[index % len(SIGNAL_COLOURS)]
        striped = index >= len(SIGNAL_COLOURS)
        colours[net] = NetColour(hex_, f"{name} striped" if striped else name, striped)
    return colours


def _pin_nets(layout: Layout) -> dict[int, str]:
    chip = P.chip(layout.chip["part"])
    nets = {chip.vplus: "vplus", chip.vminus: "vminus" if layout.build.dual_supply else GROUND}
    for use in layout.build.opamps:
        section = chip.section(use.section)
        for role, _ in PIN_ROLES:
            nets[getattr(section, role)] = getattr(use, role)
    return nets


def _pin_functions(layout: Layout) -> dict[int, str]:
    """The datasheet name of every pin, used or not, so the chip teaches its own pinout."""
    chip = P.chip(layout.chip["part"])
    functions = {chip.vplus: "V+", chip.vminus: "V-"}
    for section in chip.sections:
        for role, label in PIN_ROLES:
            functions[getattr(section, role)] = f"{label} {section.name}"
    return functions


def nets(layout: Layout) -> list[dict[str, Any]]:
    """The legend: every net, its colour, and what it joins, in the words used at the bench."""
    build, colours = layout.build, net_colours(layout)
    members: dict[str, list[str]] = {net: [] for net in colours}

    def add(net: str, text: str) -> None:
        if text not in members[net]:
            members[net].append(text)

    add("vplus", f"bottom red rail (+{build.vplus:g} V)")
    if build.dual_supply:
        add("vminus", f"top red rail ({build.vminus:g} V)")
    add(GROUND, "both blue rails")
    if layout.chip:
        functions = _pin_functions(layout)
        for pin, net in sorted(_pin_nets(layout).items()):
            add(net, f"{layout.chip['ref']} pin {pin} ({functions[pin]})")
    for part in layout.placed:
        roles = {"pot": ("end", "wiper", "end"), "led": ("anode", "cathode")}.get(part["kind"])
        for index, net in enumerate(part["nodes"]):
            add(net, f"{part['ref']} {roles[index]}" if roles else part["ref"])
    for item in layout.attachments:
        add(item["net"], f"{item['ref']} (generator +)" if item["kind"] == "source" else item["label"])
    return [{"net": net, "name": net_name(net), "color": colour.hex, "color_name": colour.name, "members": members[net]}
            for net, colour in colours.items()]


# --- the wire list -----------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    number: int
    kind: str      # power | chip | part | jumper | source | probe
    item: Any
    text: str


def steps(layout: Layout) -> list[Step]:
    """The build in order. The wire list is their text; the drawing numbers each piece to match."""
    build, colours = layout.build, net_colours(layout)
    listed: list[tuple[str, Any, str]] = [(
        "power", None,
        f"Power: +{build.vplus:g} V to the bottom red rail (bot+); "
        + (f"{build.vminus:g} V to the top red rail (top+); " if build.dual_supply else "")
        + "supply COM/ground to both blue rails, and jumper the two blue rails together at the right end of the board.",
    )]
    if layout.chip:
        chip = build.chips[layout.chip["ref"]]
        listed.append(("chip", layout.chip,
                       f"{layout.chip['ref']} {chip.part}: straddle the trench with pin 1 at f{layout.chip['col0']} "
                       f"(notch to the left); pin {chip.vplus} (V+) and pin {chip.vminus} (V-) are wired below."))
    for part in layout.placed:
        label = f"{part['ref']} {part['value']}{P.UNITS.get(part['kind'], '')}".strip()
        if part["kind"] == "pot":
            a, w, c = part["ends"]
            text = (f"{label} pot: end at {hole_name(a)}, wiper at {hole_name(w)}, other end at {hole_name(c)}."
                    f" Set to {build.pot_positions[part['ref']]:.0%} of travel from the first end.")
        elif part["kind"] == "led":
            a, k = part["ends"]
            # The value is the colour or part number, and it is on the drawing;
            # a student wiring from the list alone should see it too.
            named = f"{part['ref']} {part['value']} LED".replace("  ", " ") if part["value"] else f"{part['ref']} LED"
            text = f"{named}: anode (long leg) at {hole_name(a)}, cathode (flat side) at {hole_name(k)}."
        else:
            text = f"{label}: {hole_name(part['ends'][0])} to {hole_name(part['ends'][1])}."
        listed.append(("part", part, text))
    for jumper in layout.jumpers:
        if is_rail_bridge(jumper):
            continue
        a, b = jumper["ends"]
        listed.append(("jumper", jumper,
                       f"Jumper, {colours[jumper['net']].name} ({jumper['net']}): {hole_name(a)} to {hole_name(b)}."))
    for item in layout.attachments:
        who = "Function generator / supply lead" if item["kind"] == "source" else "Scope or meter probe"
        listed.append((item["kind"], item,
                       f"{who} {item['label']}: {hole_name(item['hole'])} (net {item['net']}); its ground clip to a blue rail."))
    return [Step(number, kind, item, text) for number, (kind, item, text) in enumerate(listed, start=1)]


def wire_list(layout: Layout) -> list[str]:
    return [step.text for step in steps(layout)]


# --- geometry ------------------------------------------------------------------

PITCH = 20.0
ROWS = {"rail-top+": 0, "rail-top-": 1, "numbers-top": 2, "a": 3, "b": 4, "c": 5, "d": 6, "e": 7,
        "trench": 8, "f": 9, "g": 10, "h": 11, "i": 12, "j": 13, "numbers-bottom": 14, "rail-bot+": 15, "rail-bot-": 16}
RAIL_ROWS = tuple(f"rail-{name}" for name in RAIL_ORDER)   # one rail order, in breadboard.py
STRIP_ROWS = {"top": "abcde", "bottom": "fghij"}
LEFT, RIGHT, TOP, BOTTOM = 66.0, 40.0, 14.0, 14.0   # rail labels sit left; the ground bridge arcs right
MIN_COLUMNS = 14
HOLE = 2.3
TAG_FONT = 8.5


def _n(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


@dataclass
class Drawing:
    first_col: int
    last_col: int
    width: float = 0.0
    height: float = 0.0
    svg: str = ""
    labels: list = field(default_factory=list)   # every tag and step badge, as boxes

    def x(self, col: int) -> float:
        return LEFT + (col - self.first_col) * PITCH

    def y(self, row: str) -> float:
        return TOP + ROWS[row] * PITCH

    def xy(self, hole: Hole) -> Point:
        if hole[0] == "rail":
            return self.x(hole[2]), self.y(f"rail-{hole[1]}")
        return self.x(hole[1]), self.y(hole[2])


def _columns_in_use(layout: Layout) -> tuple[int, int]:
    """The columns worth drawing: everything placed, one spare each side, never narrower than MIN_COLUMNS."""
    def column(hole: Hole) -> int:
        return hole[2] if hole[0] == "rail" else hole[1]

    cols = [column(hole) for part in layout.placed for hole in part["ends"]]
    cols += [column(hole) for jumper in layout.jumpers if not is_rail_bridge(jumper) for hole in jumper["ends"]]
    cols += [column(item["hole"]) for item in layout.attachments]
    if layout.chip:
        cols += [hole[1] for hole in layout.chip["pins"].values()]
    first, last = max(1, min(cols) - 1), min(COLUMNS, max(cols) + 1)
    while last - first + 1 < MIN_COLUMNS:
        if first > 1:
            first -= 1
        if last - first + 1 < MIN_COLUMNS and last < COLUMNS:
            last += 1
    return first, last


def _strip_nets(layout: Layout) -> dict[tuple, str]:
    """Which net every strip in use belongs to, so the strip can wear that net's colour."""
    owners = dict(layout.strip_net)
    if layout.chip:
        for pin, net in _pin_nets(layout).items():
            owners[strip_of(layout.chip["pins"][pin])] = net
    for part in layout.placed:
        for net, hole in zip(part["nodes"], part["ends"]):
            owners[strip_of(hole)] = net
    for jumper in layout.jumpers:
        for hole in jumper["ends"]:
            owners[strip_of(hole)] = jumper["net"]
    for item in layout.attachments:
        owners[strip_of(item["hole"])] = item["net"]
    return owners


def _overlap(a: Box, b: Box, pad: float = 0.0) -> bool:
    return a[0] - pad < b[2] and b[0] < a[2] + pad and a[1] - pad < b[3] and b[1] < a[3] + pad


def _inside(point: Point, box: Box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _bezier(a: Point, c: Point, b: Point, t: float) -> Point:
    u = 1 - t
    return u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]


def _crossing(p: list[Point], q: list[Point]) -> bool:
    """Whether two polylines cross."""
    def turn(a: Point, b: Point, c: Point) -> float:
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    return any(turn(a, b, c) * turn(a, b, d) < 0 and turn(c, d, a) * turn(c, d, b) < 0
               for a, b in zip(p, p[1:]) for c, d in zip(q, q[1:]))


def _arc(a: Point, b: Point, solids: list[Box], bounds: Box, markers: list[Point], holes: list[Point],
         middle: float, drawn: list[list[Point]]) -> Point:
    """Bow a wire so it never lies over the holes between its ends.

    Of the ways to bow it (either side, three depths), take the one that covers no
    probe or source it does not end at, then crosses the fewest wires already
    `drawn` and part bodies, hugs no other wire, covers no used hole, and stays on
    the board; then the shallowest, bowing toward the trench.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    distance = math.hypot(dx, dy)
    nx, ny = -dy / distance, dx / distance
    base = min(max(distance * 0.3, 9.0), 30.0)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    count = max(16, int(distance / 3))

    def foreign(points: list[Point]) -> list[Point]:
        return [p for p in points if math.dist(p, a) > 1 and math.dist(p, b) > 1]

    markers, holes = foreign(markers), foreign(holes)
    options = []
    for depth, scale in enumerate((1.0, 1.5, 2.0)):
        for sign in (-1, 1):
            control = (mx + nx * base * scale * sign, my + ny * base * scale * sign)
            samples = [_bezier(a, control, b, i / count) for i in range(1, count)]
            covered = sum(math.dist(p, m) < 8.5 for p in samples for m in markers)
            crossed = sum(_crossing([a, *samples, b], wire) for wire in drawn)
            hugging = sum(math.dist(p, q) < 5.0 for p in samples for wire in drawn for q in wire)
            crossings = sum(any(_inside(p, solid) for solid in solids) for p in samples)
            over = sum(math.dist(p, h) < 3.5 for p in samples for h in holes)
            off_board = sum(not _inside(p, bounds) for p in samples)
            cost = 100 * covered + 15 * crossed + 10 * crossings + 5 * off_board + 2 * over + hugging + depth
            options.append((cost, abs(control[1] - middle), control[0], control))
    return min(options)[3]


# --- parts ---------------------------------------------------------------------

@dataclass
class _Shape:
    svg: str
    box: Box
    anchor: Point
    half: tuple    # half-width and half-height of the body, for putting its tag beside it
    tag: str


BODIES = {  # fill, outline, length, width, corner radius
    "resistor": ("#D9BD87", "#8A6D3E", 24.0, 8.0, 3.5),
    "capacitor": ("#5F6B78", "#2F363D", 11.0, 11.0, 2.0),
    "inductor": ("#6E5D4B", "#3C3228", 24.0, 9.0, 4.0),
}


def _line(a: Point, b: Point, colour: str = LEAD, width: float = 1.6) -> str:
    return (f'<line x1="{_n(a[0])}" y1="{_n(a[1])}" x2="{_n(b[0])}" y2="{_n(b[1])}" '
            f'stroke="{colour}" stroke-width="{width}" stroke-linecap="round"/>')


def _tip(point: Point) -> str:
    """The bare metal end of a lead or wire, sitting in its hole."""
    return f'<circle cx="{_n(point[0])}" cy="{_n(point[1])}" r="1.7" fill="#9AA0A6" stroke="{INK}" stroke-width="0.5"/>'


def _two_terminal(part: dict, drawing: Drawing) -> _Shape:
    a, b = (drawing.xy(hole) for hole in part["ends"])
    dx, dy = b[0] - a[0], b[1] - a[1]
    distance = math.hypot(dx, dy)
    ux, uy = dx / distance, dy / distance
    fill, outline, length, width, radius = BODIES[part["kind"]]
    length = min(length, max(distance - 9, 8.0))
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    near = (cx - ux * length / 2, cy - uy * length / 2)
    far = (cx + ux * length / 2, cy + uy * length / 2)
    body = [f'<rect x="{_n(-length / 2)}" y="{_n(-width / 2)}" width="{_n(length)}" height="{_n(width)}" '
            f'rx="{radius}" fill="{fill}" stroke="{outline}" stroke-width="0.8"/>']
    if part["kind"] == "inductor":
        body += [f'<line x1="{_n(x)}" y1="{_n(-width / 2)}" x2="{_n(x)}" y2="{_n(width / 2)}" stroke="#C9B9A6" stroke-width="0.8"/>'
                 for x in (-length / 4, 0.0, length / 4)]
    if part["kind"] == "capacitor":
        body.append(f'<line x1="{_n(length / 2 - 3)}" y1="{_n(-width / 2 + 1.5)}" x2="{_n(length / 2 - 3)}" '
                    f'y2="{_n(width / 2 - 1.5)}" stroke="#C9CED3" stroke-width="1"/>')
    angle = math.degrees(math.atan2(dy, dx))
    svg = (_line(a, near) + _line(far, b)
           + f'<g transform="translate({_n(cx)} {_n(cy)}) rotate({_n(angle)})">' + "".join(body) + "</g>"
           + _tip(a) + _tip(b))
    half_w = abs(ux) * length / 2 + abs(uy) * width / 2
    half_h = abs(uy) * length / 2 + abs(ux) * width / 2
    tag = f"{part['ref']} {part['value']}{P.UNITS.get(part['kind'], '')}"
    return _Shape(svg, (cx - half_w, cy - half_h, cx + half_w, cy + half_h), (cx, cy), (half_w, half_h), tag)


def _led(part: dict, drawing: Drawing) -> _Shape:
    anode, cathode = (drawing.xy(hole) for hole in part["ends"])
    dx, dy = cathode[0] - anode[0], cathode[1] - anode[1]
    distance = math.hypot(dx, dy)
    ux, uy = dx / distance, dy / distance
    cx, cy, r = (anode[0] + cathode[0]) / 2, (anode[1] + cathode[1]) / 2, 6.5
    flat = (cx + ux * r * 0.75, cy + uy * r * 0.75)
    px, py = -uy * r * 0.7, ux * r * 0.7
    svg = (_line(anode, (cx - ux * r, cy - uy * r)) + _line((cx + ux * r, cy + uy * r), cathode)
           + f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{r}" fill="#F4C542" fill-opacity="0.92" stroke="#B38600" stroke-width="0.9"/>'
           + _line((flat[0] + px, flat[1] + py), (flat[0] - px, flat[1] - py), "#8A6800", 1.6)
           + _tip(anode) + _tip(cathode))
    tag = f"{part['ref']} {part['value']}".strip()
    return _Shape(svg, (cx - r, cy - r, cx + r, cy + r), (cx, cy), (r, r), tag)


def _pot(part: dict, drawing: Drawing, position: float) -> _Shape:
    first, wiper, last = (drawing.xy(hole) for hole in part["ends"])
    cx, cy = wiper
    half_w = abs(last[0] - first[0]) / 2 + 8
    half_h = abs(last[1] - first[1]) / 2 + 10
    # The knob points where the student should turn it: a 270-degree sweep from the first end.
    sweep = 1 if (first[0], first[1]) <= (last[0], last[1]) else -1
    angle = math.radians(sweep * (-135 + 270 * position))
    pointer = (cx + math.sin(angle) * 5.2, cy - math.cos(angle) * 5.2)
    pads = "".join(f'<rect x="{_n(x - 2.4)}" y="{_n(y - 2.4)}" width="4.8" height="4.8" rx="1" fill="#C9CED3"/>' for x, y in (first, last))
    svg = (f'<rect x="{_n(cx - half_w)}" y="{_n(cy - half_h)}" width="{_n(2 * half_w)}" height="{_n(2 * half_h)}" '
           f'rx="4" fill="#34404E" stroke="#1C232C" stroke-width="0.9"/>' + pads
           + f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="6.5" fill="#E8EBEE" stroke="#1C232C" stroke-width="0.9"/>'
           + _line((cx, cy), pointer, INK, 1.6))
    tag = f"{part['ref']} {part['value']}{P.UNITS['pot']} · {position:.0%}"
    return _Shape(svg, (cx - half_w, cy - half_h, cx + half_w, cy + half_h), (cx, cy), (half_w, half_h), tag)


def _chip(layout: Layout, drawing: Drawing, step: int) -> tuple[str, Box]:
    col0 = layout.chip["col0"]
    x0, x1 = drawing.x(col0) - 9, drawing.x(col0 + 6) + 9
    ye, yf = drawing.y("e"), drawing.y("f")
    top, bottom, cy = ye - 9, yf + 9, (ye + yf) / 2
    pin_nets, functions = _pin_nets(layout), _pin_functions(layout)
    out = [f'<g class="chip" data-step="{step}" data-nets="{escape(" ".join(dict.fromkeys(pin_nets.values())))}">',
           f'<rect class="chip-body" x="{_n(x0)}" y="{_n(top)}" width="{_n(x1 - x0)}" height="{_n(bottom - top)}" rx="3" fill="#2B2F33"/>',
           f'<path d="M {_n(x0)} {_n(cy - 5)} A 5 5 0 0 1 {_n(x0)} {_n(cy + 5)}" fill="{BOARD}"/>',
           f'<circle cx="{_n(x0 + 4)}" cy="{_n(yf + 1)}" r="1.6" fill="#9AA0A6"/>',
           f'<text x="{_n((x0 + x1) / 2)}" y="{_n(cy + 3.2)}" font-size="9" font-weight="700" text-anchor="middle" '
           f'fill="#F1F3F4">{escape(layout.chip["ref"])} {escape(layout.chip["part"])}</text>']
    for pin, hole in sorted(layout.chip["pins"].items()):
        x, y = drawing.xy(hole)
        net = pin_nets.get(pin)
        number_fill, function_fill = ("#F1F3F4", "#C9CED3") if net else ("#7D848C", "#5C636B")
        function_y = y - 7 if hole[0] == "bottom" else y + 12
        out.append((f'<g class="pin" data-nets="{escape(net)}">' if net else '<g class="pin">')
                   + f'<text class="pin-number" x="{_n(x)}" y="{_n(y + 3)}" font-size="6.5" text-anchor="middle" fill="{number_fill}">{pin}</text>'
                   + f'<text class="pin-function" data-pin="{pin}" x="{_n(x)}" y="{_n(function_y)}" font-size="5.2" '
                     f'text-anchor="middle" fill="{function_fill}">{functions[pin]}</text></g>')
    out.append("</g>")
    return "".join(out), (x0, top, x1, bottom)


# --- labels --------------------------------------------------------------------

@dataclass
class _Labeller:
    """Puts each tag where it covers nothing a student needs to see.

    Hard obstacles (used holes, part bodies, the chip, probe markers, the
    margins, and every tag already placed) are refused outright whenever any
    candidate avoids them; among the rest, the nearest spot that crosses the
    fewest wires wins.
    """
    bounds: Box
    blocked: list
    avoid: list
    placed: list = field(default_factory=list)

    def _cost(self, box: Box, anchor: Point) -> float:
        hits = sum(_overlap(box, other) for other in self.blocked) + sum(_overlap(box, other, pad=1.0) for other in self.placed)
        if not (_inside(box[:2], self.bounds) and _inside(box[2:], self.bounds)):
            hits += 1
        crossed = sum(_inside(point, box) for point in self.avoid)
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        return hits * 10_000 + crossed * 40 + math.hypot(centre[0] - anchor[0], centre[1] - anchor[1])

    def _clamped(self, box: Box) -> Box:
        """Shift a box inside the board, keeping its size.

        The cost function already prefers a candidate that fits, but "prefers"
        is not "guarantees": with every spot blocked, the cheapest one can still
        sit off the edge, and a tag off the edge is a tag the student never
        reads. Shifting keeps it legible where merely clipping would not.
        """
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        left = min(max(left, self.bounds[0]), max(self.bounds[0], self.bounds[2] - width))
        top = min(max(top, self.bounds[1]), max(self.bounds[1], self.bounds[3] - height))
        return (left, top, left + width, top + height)

    def place(self, anchor: Point, candidates: list) -> Box:
        best = self._clamped(min(candidates, key=lambda box: self._cost(box, anchor)))
        self.placed.append(best)
        return best


def _around(anchor: Point, half: tuple, size: tuple) -> list:
    """Spots above, below, and to either side of a body, stepping outward."""
    (ax, ay), (hw, hh), (w, h) = anchor, half, size
    boxes = []
    for ring in range(6):
        gap = 3 + ring * PITCH / 2
        for shift in (0.0, -PITCH, PITCH, -2 * PITCH, 2 * PITCH):
            left = ax - w / 2 + shift
            boxes.append((left, ay - hh - gap - h, left + w, ay - hh - gap))
            boxes.append((left, ay + hh + gap, left + w, ay + hh + gap + h))
        for shift in (0.0, -PITCH / 2, PITCH / 2, -PITCH, PITCH):
            top = ay - h / 2 + shift
            boxes.append((ax + hw + gap, top, ax + hw + gap + w, top + h))
            boxes.append((ax - hw - gap - w, top, ax - hw - gap, top + h))
    return boxes


def _tag_size(text: str, bar: bool) -> tuple:
    return (4 if bar else 0) + 17 + len(text) * TAG_FONT * 0.6 + 5, 14.0


def _tag(box: Box, step: int, nets_attr: str, text: str, bar: str | None = None) -> str:
    x0, y0, x1, y1 = box
    cy, inset = (y0 + y1) / 2, 4 if bar else 0
    out = [f'<g class="tag" data-step="{step}" data-nets="{nets_attr}">',
           f'<rect x="{_n(x0)}" y="{_n(y0)}" width="{_n(x1 - x0)}" height="{_n(y1 - y0)}" rx="3" fill="#FFFFFF" stroke="{INK}" stroke-width="0.8"/>']
    if bar:
        out.append(f'<rect x="{_n(x0)}" y="{_n(y0)}" width="4" height="{_n(y1 - y0)}" rx="1.5" fill="{bar}"/>')
    out.append(f'<circle cx="{_n(x0 + inset + 8)}" cy="{_n(cy)}" r="5.3" fill="{INK}"/>')
    out.append(f'<text x="{_n(x0 + inset + 8)}" y="{_n(cy + 2.3)}" font-size="6.5" font-weight="700" text-anchor="middle" fill="#FFFFFF">{step}</text>')
    out.append(f'<text x="{_n(x0 + inset + 16)}" y="{_n(cy + 3)}" font-size="{TAG_FONT}" fill="{INK}">{escape(text)}</text>')
    out.append("</g>")
    return "".join(out)


def _badge(box: Box, step: int, net: str, colour: NetColour) -> str:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (f'<g class="badge" data-step="{step}" data-nets="{escape(net)}">'
            f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="6" fill="#FFFFFF" stroke="{colour.hex}" stroke-width="1.8"/>'
            f'<text x="{_n(cx)}" y="{_n(cy + 2.4)}" font-size="7" font-weight="700" text-anchor="middle" fill="{INK}">{step}</text></g>')


def _leader(box: Box, anchor: Point, half: tuple, step: int, nets_attr: str) -> str:
    """A hairline from a tag that had to move away back to what it names."""
    nearest = (min(max(anchor[0], box[0]), box[2]), min(max(anchor[1], box[1]), box[3]))
    if math.hypot(anchor[0] - nearest[0], anchor[1] - nearest[1]) <= max(half) + 5:
        return ""
    return f'<g data-step="{step}" data-nets="{nets_attr}">{_line(nearest, anchor, SILK, 0.8)}</g>'


def _quad(a: Point, c: Point, b: Point) -> str:
    return f"M {_n(a[0])} {_n(a[1])} Q {_n(c[0])} {_n(c[1])} {_n(b[0])} {_n(b[1])}"


def _wire(net: str, colour: NetColour, d: str, a: Point, b: Point, step: int) -> str:
    stripe = (f'<path d="{d}" fill="none" stroke="#FFFFFF" stroke-width="0.9" stroke-dasharray="2.5 2.5"/>'
              if colour.striped else "")
    return (f'<g class="jumper" data-step="{step}" data-nets="{escape(net)}">'
            f'<path d="{d}" fill="none" stroke="{INK}" stroke-opacity="0.35" stroke-width="4.6" stroke-linecap="round"/>'
            f'<path class="wire" d="{d}" stroke="{colour.hex}" fill="none" stroke-width="2.9" stroke-linecap="round"/>'
            + stripe + _tip(a) + _tip(b) + "</g>")


# --- the drawing -----------------------------------------------------------------

def draw(layout: Layout) -> Drawing:
    first, last = _columns_in_use(layout)
    drawing = Drawing(first, last)
    drawing.width = LEFT + (last - first) * PITCH + RIGHT
    drawing.height = TOP + 16 * PITCH + BOTTOM
    build, colours = layout.build, net_colours(layout)
    step_of = {id(step.item): step.number for step in steps(layout) if step.item is not None}

    shapes = []
    for part in layout.placed:
        if part["kind"] == "pot":
            shape = _pot(part, drawing, build.pot_positions[part["ref"]])
        elif part["kind"] == "led":
            shape = _led(part, drawing)
        else:
            shape = _two_terminal(part, drawing)
        shapes.append((part, shape))
    chip_svg, chip_box = _chip(layout, drawing, step_of[id(layout.chip)]) if layout.chip else ("", None)
    solids = [shape.box for _, shape in shapes] + ([chip_box] if chip_box else [])
    board = (LEFT - 12, TOP, drawing.width - RIGHT + 12, drawing.height - BOTTOM)
    used = [hole for part in layout.placed for hole in part["ends"]]
    used += [hole for jumper in layout.jumpers if not is_rail_bridge(jumper) for hole in jumper["ends"]]
    used += [item["hole"] for item in layout.attachments]
    markers = [drawing.xy(item["hole"]) for item in layout.attachments]
    wires, drawn = [], []
    for jumper in layout.jumpers:
        if not is_rail_bridge(jumper):
            a, b = (drawing.xy(hole) for hole in jumper["ends"])
            control = _arc(a, b, solids, board, markers, [drawing.xy(hole) for hole in used], drawing.y("trench"), drawn)
            wires.append((jumper, a, control, b))
            drawn.append([_bezier(a, control, b, i / 24) for i in range(25)])
    blocked = [(x - HOLE - 1, y - HOLE - 1, x + HOLE + 1, y + HOLE + 1) for x, y in map(drawing.xy, used)]
    blocked += solids
    blocked += [(0.0, 0.0, LEFT - 9, drawing.height), (drawing.width - RIGHT + 9, 0.0, drawing.width, drawing.height)]
    blocked += [(x - 6.5, y - 6.5, x + 6.5, y + 6.5) for x, y in (drawing.xy(item["hole"]) for item in layout.attachments)]
    avoid = [_bezier(a, c, b, i / 8) for _, a, c, b in wires for i in range(1, 8)]
    avoid += [(drawing.x(col), drawing.y(row)) for col in range(first, last + 1)
              for row in ("numbers-top", "numbers-bottom", *RAIL_ROWS)]
    for part, _ in shapes:
        ends = [drawing.xy(hole) for hole in part["ends"]]
        avoid += [((p[0] * 3 + q[0]) / 4, (p[1] * 3 + q[1]) / 4) for p, q in zip(ends, ends[1:])]
        avoid += [((p[0] + q[0] * 3) / 4, (p[1] + q[1] * 3) / 4) for p, q in zip(ends, ends[1:])]
    labeller = _Labeller((2.0, 2.0, drawing.width - 2, drawing.height - 2), blocked, avoid)

    # Short wires have the least room for their number, so they choose first.
    badges = []
    for jumper, a, c, b in sorted(wires, key=lambda wire: math.dist(wire[1], wire[3])):
        spots = [_bezier(a, c, b, 0.5 + sign * k / 20) for k in range(7) for sign in ((1,) if k == 0 else (-1, 1))]
        badges.append((jumper, labeller.place(spots[0], [(x - 6, y - 6, x + 6, y + 6) for x, y in spots])))
    tags, probe_index = [], 0
    for item in layout.attachments:
        if item["kind"] == "probe":
            colour, probe_index = PROBE_COLOURS[probe_index % len(PROBE_COLOURS)], probe_index + 1
        else:
            colour = INK
        spot = drawing.xy(item["hole"])
        tags.append((item, colour, labeller.place(spot, _around(spot, (6.5, 6.5), _tag_size(item["label"], bar=True)))))
    part_tags = [(part, shape, labeller.place(shape.anchor, _around(shape.anchor, shape.half, _tag_size(shape.tag, bar=False))))
                 for part, shape in shapes]
    drawing.labels = list(labeller.placed)

    width, height = drawing.width, drawing.height
    span = (drawing.x(first) - 10, drawing.x(last) + 10)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(width)} {_n(height)}" width="100%" role="img" '
           f'aria-label="breadboard layout" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">',
           f'<defs><rect id="h" x="-2.3" y="-2.3" width="4.6" height="4.6" rx="1" fill="{HOLE_FILL}"/></defs>',
           f'<rect x="0" y="0" width="{_n(width)}" height="{_n(height)}" rx="8" fill="{BOARD}"/>',
           f'<rect x="{_n(span[0])}" y="{_n(drawing.y("trench") - 5)}" width="{_n(span[1] - span[0])}" height="10" rx="2" fill="{TRENCH}"/>']

    # The copper under the plastic: every strip in use, in its net's colour.
    for (side, key), net in sorted(_strip_nets(layout).items(), key=str):
        colour = colours[net].hex
        if side == "rail":
            y = drawing.y(f"rail-{key}")
            out.append(f'<rect class="strip" data-nets="{escape(net)}" x="{_n(span[0])}" y="{_n(y - 6)}" '
                       f'width="{_n(span[1] - span[0])}" height="12" rx="6" fill="{colour}" fill-opacity="0.18"/>')
        else:
            rows = STRIP_ROWS[side]
            x, y0, y1 = drawing.x(key), drawing.y(rows[0]) - 7.5, drawing.y(rows[-1]) + 7.5
            out.append(f'<rect class="strip" data-nets="{escape(net)}" x="{_n(x - 7.5)}" y="{_n(y0)}" '
                       f'width="15" height="{_n(y1 - y0)}" rx="5" fill="{colour}" fill-opacity="0.22"/>')

    for col in range(first, last + 1):
        x = drawing.x(col)
        out += [f'<use href="#h" x="{_n(x)}" y="{_n(drawing.y(row))}"/>' for row in (*"abcdefghij", *RAIL_ROWS)]
        out += [f'<text x="{_n(x)}" y="{_n(drawing.y(row) + 2.5)}" font-size="7" text-anchor="middle" fill="{SILK}">{col}</text>'
                for row in ("numbers-top", "numbers-bottom")]
    out += [f'<text x="{_n(LEFT - 14)}" y="{_n(drawing.y(row) + 2.5)}" font-size="7.5" text-anchor="middle" fill="{SILK}">{row}</text>'
            for row in "abcdefghij"]

    power_nets = " ".join(net for net in ("vplus", "vminus", GROUND) if net in colours)
    out.append(f'<g class="power" data-step="1" data-nets="{power_nets}">')
    for rail, stripe, offset in (("top+", "#D23A30", -9), ("top-", "#2F66C9", 9), ("bot+", "#D23A30", -9), ("bot-", "#2F66C9", 9)):
        y = drawing.y(f"rail-{rail}") + offset
        out.append(_line((span[0], y), (span[1], y), stripe, 1.1))
    rail_labels = {
        "top+": ("V-", colours["vminus"].hex, f"{build.vminus:g} V") if build.dual_supply else ("unused", SILK, ""),
        "top-": ("GND", INK, ""),
        "bot+": ("V+", colours["vplus"].hex, f"+{build.vplus:g} V"),
        "bot-": ("GND", INK, ""),
    }
    for rail, (name, fill, volts) in rail_labels.items():
        y = drawing.y(f"rail-{rail}") + 3
        out.append(f'<text x="8" y="{_n(y)}" font-size="9" font-weight="700" fill="{fill}">{name}</text>')
        if volts:
            out.append(f'<text x="27" y="{_n(y)}" font-size="7" fill="{SILK}">{volts}</text>')
    # The ground bridge leaves the board sideways and runs down the right margin.
    edge, run, bend = drawing.x(last), drawing.x(last) + 24, 8.0
    y0, y1 = drawing.y("rail-top-"), drawing.y("rail-bot-")
    bridge = (f"M {_n(edge)} {_n(y0)} L {_n(run - bend)} {_n(y0)} Q {_n(run)} {_n(y0)} {_n(run)} {_n(y0 + bend)} "
              f"L {_n(run)} {_n(y1 - bend)} Q {_n(run)} {_n(y1)} {_n(run - bend)} {_n(y1)} L {_n(edge)} {_n(y1)}")
    out.append(_wire(GROUND, colours[GROUND], bridge, (edge, y0), (edge, y1), 1))
    out.append("</g>")

    for part, shape, box in part_tags:
        out.append(_leader(box, shape.anchor, shape.half, step_of[id(part)], escape(" ".join(dict.fromkeys(part["nodes"])))))
    for item, _, box in tags:
        out.append(_leader(box, drawing.xy(item["hole"]), (6.5, 6.5), step_of[id(item)], escape(item["net"])))
    out.append(chip_svg)
    for part, shape in shapes:
        out.append(f'<g class="part" data-step="{step_of[id(part)]}" data-nets="{escape(" ".join(dict.fromkeys(part["nodes"])))}">{shape.svg}</g>')
    for jumper, a, c, b in wires:
        out.append(_wire(jumper["net"], colours[jumper["net"]], _quad(a, c, b), a, b, step_of[id(jumper)]))
    for item, colour, _ in tags:
        x, y = drawing.xy(item["hole"])
        out.append(f'<g class="{item["kind"]}" data-step="{step_of[id(item)]}" data-nets="{escape(item["net"])}">'
                   f'<circle cx="{_n(x)}" cy="{_n(y)}" r="5.4" fill="none" stroke="{colour}" stroke-width="2"/>'
                   f'<circle cx="{_n(x)}" cy="{_n(y)}" r="1.9" fill="{colour}"/></g>')
    for jumper, box in badges:
        out.append(_badge(box, step_of[id(jumper)], jumper["net"], colours[jumper["net"]]))
    for item, colour, box in tags:
        out.append(_tag(box, step_of[id(item)], escape(item["net"]), item["label"], colour))
    for part, shape, box in part_tags:
        out.append(_tag(box, step_of[id(part)], escape(" ".join(dict.fromkeys(part["nodes"]))), shape.tag))
    out.append("</svg>")
    drawing.svg = "".join(out)
    return drawing


def svg(layout: Layout) -> str:
    return draw(layout).svg


def layout_payload(content: Any) -> dict[str, Any]:
    layout = place(parse_build(content))
    return {"svg": svg(layout), "wires": wire_list(layout),
            "holes": {part["ref"]: [hole_name(hole) for hole in part["ends"]] for part in layout.placed},
            "probes": [{"label": item["label"], "hole": hole_name(item["hole"]), "net": item["net"]}
                       for item in layout.attachments if item["kind"] == "probe"],
            "nets": nets(layout)}
