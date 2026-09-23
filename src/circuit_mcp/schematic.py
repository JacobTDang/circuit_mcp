"""Netlist -> schematic drawing, from the same netlist ``derive`` verified.

A drawing that has drifted from its netlist is worse than no drawing: it looks
like the circuit that was checked. So the layout is not trusted. Every drawing
is read back out of its own geometry -- wires joined where they share an end,
where a dot marks a junction, and where they run over one another, and joined
nowhere else -- and refused unless the connections that come back are the ones
the netlist declares.

The shapes this lays out are the ones the course draws: op-amp stages left to
right, the inverting input on top, input branches stacked away from the pin
they feed, feedback on a track above the triangle, and anything tied to ground
dropped onto a ground symbol. A netlist it cannot place is refused by name
rather than drawn approximately.

Only the repo's own SVG is used. schemdraw would be a new dependency that still
leaves every element to be placed by hand, and lcapy's own ``draw()`` needs
LaTeX, circuitikz and pdf2svg, none of which are installed, plus a direction
hint on every element that a ``derive`` netlist does not carry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from typing import Any

GROUND = "0"
PITCH = 75.0          # one branch row, wide enough for a label above the symbol
STAGE_WIDTH = 340.0   # one op-amp stage, left edge to left edge
BODY = 60.0           # the drawn length of a two-terminal symbol
MARGIN = 40.0
FONT = 11.5
CHAR = 6.0            # a label character's width at that size
UNITS = {"R": "Ω", "C": "F", "L": "H", "V": "V", "I": "A"}
PREFIXES = ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"),
            (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"))
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
Point = tuple[float, float]
Box = tuple[float, float, float, float]


class SchematicError(ValueError):
    """A netlist this cannot draw, or a drawing that no longer says its netlist."""


@dataclass(frozen=True)
class Element:
    name: str
    kind: str                 # R, C, L, V, I, or opamp
    nodes: tuple[str, ...]    # for an op amp: (out, out_ref, in_plus, in_minus)
    value: str


@dataclass
class Drawing:
    elements: list[Element]
    symbols: list[dict[str, Any]] = field(default_factory=list)
    wires: list[dict[str, Any]] = field(default_factory=list)
    dots: list[Point] = field(default_factory=list)
    grounds: list[Point] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)
    terminals: dict[str, list[Point]] = field(default_factory=dict)
    width: float = 0.0
    height: float = 0.0


# --- reading the netlist ------------------------------------------------------

def parse(netlist: str) -> list[Element]:
    """The elements this can draw, or a refusal naming the one it cannot."""
    elements: list[Element] = []
    for line in netlist.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith((";", "#", "*", ".")):
            continue
        name = fields[0]
        if not NAME.match(name):
            raise SchematicError(f"{name!r} is not a usable element name")
        letter = name[0].upper()
        if len(fields) > 3 and fields[3] == "opamp":
            if len(fields) < 6:
                raise SchematicError(f"{name}: an op amp needs an output pair and two inputs")
            out, out_ref, in_plus, in_minus = fields[1], fields[2], fields[4], fields[5]
            if out_ref != GROUND:
                raise SchematicError(
                    f"{name}: this draws an op amp whose output is referenced to ground, "
                    f"and this one is referenced to node {out_ref!r}."
                )
            elements.append(Element(name, "opamp", (out, out_ref, in_plus, in_minus), ""))
            continue
        if letter not in UNITS:
            raise SchematicError(
                f"{name}: {_describe(letter)} cannot be drawn by this tool. It draws "
                f"resistors, capacitors, inductors, independent sources and ideal op amps."
            )
        if len(fields) < 3:
            raise SchematicError(f"{name}: needs two nodes")
        elements.append(Element(name, letter, (fields[1], fields[2]), " ".join(fields[3:])))
    if not elements:
        raise SchematicError("the netlist has no elements to draw")
    return elements


def _describe(letter: str) -> str:
    return {
        "E": "a voltage-controlled voltage source",
        "F": "a current-controlled current source",
        "G": "a voltage-controlled current source",
        "H": "a current-controlled voltage source",
        "K": "a coupled-inductor statement",
        "W": "a wire element",
    }.get(letter, f"an element of type {letter!r}")


def _value(element: Element) -> str:
    """A value a student would read, or the symbol the netlist used."""
    text = element.value.strip().strip("{}")
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    for scale, prefix in PREFIXES:
        if abs(number) >= scale or scale == 1e-12:
            sized = number / scale
            digits = f"{sized:.10g}"
            return f"{digits} {prefix}{UNITS.get(element.kind, '')}".strip()
    return text


# --- laying it out ------------------------------------------------------------

def _touching(elements: list[Element], node: str) -> list[Element]:
    return [e for e in elements if e.kind != "opamp" and node in e.nodes]


def draw(netlist: str) -> Drawing:
    """Place every element, then refuse the result unless it rebuilds the netlist."""
    elements = parse(netlist)
    stages = [e for e in elements if e.kind == "opamp"]
    if not stages:
        raise SchematicError(
            "this draws op-amp circuits, and the netlist has no op amp in it"
        )
    drawing = _place(elements, _ordered(elements, stages))
    verify(drawing)
    return drawing


def _ordered(elements: list[Element], stages: list[Element]) -> list[Element]:
    """Signal order: a stage driven by another stage's output comes after it."""
    outputs = {stage.nodes[0]: stage for stage in stages}
    depth: dict[str, int] = {}

    def rank(stage: Element, seen: frozenset[str]) -> int:
        if stage.name in depth:
            return depth[stage.name]
        if stage.name in seen:
            raise SchematicError(f"{stage.name}: the stages feed each other in a loop")
        best = 0
        for node in stage.nodes[2:]:
            for reached in _reachable(elements, node):
                driver = outputs.get(reached)
                if driver is not None and driver.name != stage.name:
                    best = max(best, rank(driver, seen | {stage.name}) + 1)
        depth[stage.name] = best
        return best

    return sorted(stages, key=lambda stage: (rank(stage, frozenset()), stage.name))


def _reachable(elements: list[Element], node: str) -> set[str]:
    """Nodes one element away, which is how far a stage's input network reaches."""
    return {other for element in _touching(elements, node) for other in element.nodes}


def _place(elements: list[Element], stages: list[Element]) -> Drawing:
    drawing = Drawing(elements=elements)
    placed: set[str] = set()
    buses: dict[str, Point] = {}
    for index, stage in enumerate(stages):
        _place_stage(drawing, elements, stage, index, placed, buses)
    unplaced = [e.name for e in elements if e.kind != "opamp" and e.name not in placed]
    if unplaced:
        raise SchematicError(
            f"{', '.join(unplaced)}: not attached to any op-amp input, feedback path or "
            f"output, so this layout has nowhere to put {'them' if len(unplaced) > 1 else 'it'}."
        )
    _size(drawing)
    return drawing


def _place_stage(drawing: Drawing, elements: list[Element], stage: Element,
                 index: int, placed: set[str], buses: dict[str, Point]) -> None:
    out, _, plus, minus = stage.nodes
    x = MARGIN + 260 + index * STAGE_WIDTH
    y = 300.0
    drawing.symbols.append({"shape": "opamp", "points": [(x, y - 45), (x, y + 45), (x + 70, y)],
                            "name": stage.name})
    minus_pin, plus_pin, out_pin = (x, y - 22), (x, y + 22), (x + 70, y)
    drawing.terminals[stage.name] = [out_pin, minus_pin, plus_pin]
    drawing.labels.append(_label(stage.name, (x + 26, y - 58)))
    drawing.labels.append(_label("-", (x + 9, y - 22), small=True))
    drawing.labels.append(_label("+", (x + 9, y + 22), small=True))

    feedback = [e for e in _touching(elements, minus) if out in e.nodes]
    inputs = {
        minus: [] if minus == GROUND else [e for e in _touching(elements, minus) if e not in feedback],
        plus: [] if plus == GROUND else _touching(elements, plus),
    }
    bus_x = x - 70
    rows: dict[str, list[float]] = {}
    # One column per branch across the whole stage: both inputs draw to the left,
    # and two branches sharing a column would run their verticals over each other.
    taken: list[float] = []
    for pin, node, step in ((minus_pin, minus, -PITCH), (plus_pin, plus, PITCH)):
        drawing.wires.append({"points": [pin, (bus_x, pin[1])], "net": node})
        if node == GROUND:
            drawing.wires.append({"points": [(bus_x, pin[1]), (bus_x, pin[1] + 40)], "net": GROUND})
            drawing.grounds.append((bus_x, pin[1] + 40))
            rows[node] = [pin[1]]
            continue
        rows[node] = _branches(drawing, elements, inputs[node], node, bus_x, pin[1], step,
                               placed, buses, taken)
        if len(rows[node]) > 1:
            drawing.wires.append({"points": [(bus_x, pin[1]), (bus_x, rows[node][-1])], "net": node})
            drawing.dots.append((bus_x, pin[1]))

    out_bus = x + 130
    drawing.wires.append({"points": [out_pin, (out_bus, y)], "net": out})
    buses[out] = (out_bus, y)
    top = min(rows.get(minus, [y - 22]) + [y - 22])
    for depth, element in enumerate(feedback):
        track = top - 80 - depth * PITCH
        centre = (bus_x + out_bus) / 2
        left_end, right_end = (centre - BODY / 2, track), (centre + BODY / 2, track)
        # Terminal 0 is nodes[0]: flipped when the output end carries nodes[0].
        _two_terminal(drawing, element, left_end, right_end, placed, flipped=element.nodes[0] == out)
        drawing.wires.append({"points": [(bus_x, track), left_end], "net": minus})
        drawing.wires.append({"points": [right_end, (out_bus, track)], "net": out})
        drawing.wires.append({"points": [(bus_x, track), (bus_x, y - 22)], "net": minus})
        drawing.wires.append({"points": [(out_bus, track), (out_bus, y)], "net": out})
        drawing.dots.extend([(bus_x, y - 22), (out_bus, y)])
    if minus == out:
        # A follower: the output is the inverting input, with no element between.
        track = top - 80
        drawing.wires.append({"points": [(bus_x, track), (out_bus, track)], "net": out})
        drawing.wires.append({"points": [(bus_x, track), (bus_x, y - 22)], "net": out})
        drawing.wires.append({"points": [(out_bus, track), (out_bus, y)], "net": out})
        drawing.dots.extend([(bus_x, y - 22), (out_bus, y)])
    _outputs(drawing, elements, out, out_bus, y, placed)


def _branches(drawing: Drawing, elements: list[Element], branches: list[Element], node: str,
              bus_x: float, pin_y: float, step: float, placed: set[str],
              buses: dict[str, Point], taken: list[float]) -> list[float]:
    """One branch per row, each with its own column for whatever ends it.

    Every vertical belonging to a branch sits left of every symbol, so a drop to
    ground crosses other rows without ever ending on one -- a crossing joins
    nothing, and that is what keeps these rows independent.
    """
    rows: list[float] = []
    for depth, element in enumerate(branches):
        row = pin_y + step * depth
        rows.append(row)
        column = bus_x - BODY - 40 - len(taken) * 95
        taken.append(column)
        far = element.nodes[0] if element.nodes[1] == node else element.nodes[1]
        if element.kind in ("V", "I"):
            _source_at(drawing, element, node, bus_x, row, column, placed, step)
        else:
            _two_terminal(drawing, element, (bus_x - BODY, row), (bus_x, row), placed,
                          flipped=element.nodes[0] == node)
            drawing.wires.append({"points": [(bus_x - BODY, row), (column, row)], "net": far})
            _far_end(drawing, elements, far, (column, row), placed, buses, step)
        if row != pin_y:
            drawing.wires.append({"points": [(bus_x, row), (bus_x, pin_y)], "net": node})
    return rows


def _drop_to_ground(drawing: Drawing, at: Point, step: float) -> None:
    """Downwards, always: ground is drawn below the thing it grounds.

    The drop crosses the rows beneath it, which joins nothing -- each branch
    owns its own column, so a crossing is only ever a crossing.
    """
    x, y = at
    end = y + 55
    drawing.wires.append({"points": [(x, y), (x, end)], "net": GROUND})
    drawing.grounds.append((x, end))


def _source_at(drawing: Drawing, element: Element, node: str, bus_x: float, row: float,
               column: float, placed: set[str], step: float) -> None:
    """A source wired straight onto an input, standing in its own column."""
    far = element.nodes[0] if element.nodes[1] == node else element.nodes[1]
    if far != GROUND:
        raise SchematicError(
            f"{element.name}: drives an op-amp input but is not referenced to ground, "
            f"which this layout has no channel for."
        )
    top = (column, row)
    bottom = (column, row + 55)
    drawing.wires.append({"points": [(bus_x, row), top], "net": node})
    drawing.symbols.append({"shape": "source", "centre": ((top[0] + bottom[0]) / 2, (top[1] + bottom[1]) / 2),
                            "kind": element.kind, "name": element.name})
    drawing.terminals[element.name] = [top, bottom] if element.nodes[0] == node else [bottom, top]
    placed.add(element.name)
    drawing.labels.append(_beside(f"{element.name} {_value(element)}".strip(),
                                  column - 24, (top[1] + bottom[1]) / 2))
    _drop_to_ground(drawing, bottom, step)


def _far_end(drawing: Drawing, elements: list[Element], node: str, at: Point,
             placed: set[str], buses: dict[str, Point], step: float) -> None:
    """Ground, a source of its own, an earlier stage's output, or a named port."""
    x, y = at
    if node == GROUND:
        _drop_to_ground(drawing, at, step)
        return
    if node in buses:
        # A later stage fed by an earlier one: run back along this row to its bus.
        bus = buses[node]
        drawing.wires.append({"points": [(x, y), (bus[0], y)], "net": node})
        drawing.wires.append({"points": [(bus[0], y), bus], "net": node})
        drawing.dots.append(bus)
        return
    source = next((e for e in elements if e.kind in ("V", "I") and node in e.nodes
                   and e.name not in placed), None)
    if source is not None:
        other = source.nodes[0] if source.nodes[1] == node else source.nodes[1]
        if other != GROUND:
            raise SchematicError(
                f"{source.name}: is not referenced to ground, which this layout has no channel for."
            )
        bottom = (x, y + 55)
        drawing.symbols.append({"shape": "source", "centre": (x, (y + bottom[1]) / 2),
                                "kind": source.kind, "name": source.name})
        drawing.terminals[source.name] = [at, bottom] if source.nodes[0] == node else [bottom, at]
        placed.add(source.name)
        drawing.labels.append(_beside(f"{source.name} {_value(source)}".strip(),
                                      x - 24, (y + bottom[1]) / 2))
        _drop_to_ground(drawing, bottom, step)
        return
    drawing.labels.append(_label(node, (x - 22, y)))


def _outputs(drawing: Drawing, elements: list[Element], node: str,
             bus_x: float, y: float, placed: set[str]) -> None:
    """Loads hanging off the output. Anything leading onward belongs to that stage."""
    loads = [e for e in _touching(elements, node)
             if e.name not in placed and GROUND in e.nodes and e.kind in ("R", "C", "L")]
    for depth, element in enumerate(loads):
        x = bus_x + 45 + depth * 70
        drawing.wires.append({"points": [(bus_x, y), (x, y)], "net": node})
        _two_terminal(drawing, element, (x, y), (x, y + BODY), placed,
                      flipped=element.nodes[0] == GROUND, vertical=True)
        _drop_to_ground(drawing, (x, y + BODY), 1.0)
        drawing.dots.append((bus_x, y))
    drawing.labels.append(_label(node, (bus_x + 18, y - 16)))


def _two_terminal(drawing: Drawing, element: Element, first: Point, second: Point,
                  placed: set[str], flipped: bool = False, vertical: bool = False) -> None:
    drawing.symbols.append({"shape": element.kind, "from": first, "to": second,
                            "name": element.name, "vertical": vertical})
    ends = [second, first] if flipped else [first, second]
    drawing.terminals[element.name] = ends
    placed.add(element.name)
    text = f"{element.name} {_value(element)}".strip()
    if vertical:
        anchor = ((first[0] + second[0]) / 2 + 10 + CHAR * len(text) / 2, (first[1] + second[1]) / 2)
    else:
        anchor = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2 - 20)
    drawing.labels.append(_label(text, anchor))


def _beside(text: str, right_edge: float, y: float) -> dict[str, Any]:
    """A label whose right edge sits where it is put, for text left of a symbol."""
    return _label(text, (right_edge - max(CHAR * len(text) / 2, 4.0), y))


def _label(text: str, anchor: Point, small: bool = False) -> dict[str, Any]:
    size = FONT * (0.9 if small else 1.0)
    half_width = max(CHAR * len(text) / 2, 4.0)
    x, y = anchor
    return {"text": text, "at": anchor, "size": size,
            "box": (x - half_width, y - size / 2 - 1, x + half_width, y + size / 2 + 1)}


def _size(drawing: Drawing) -> None:
    xs = [x for wire in drawing.wires for x, _ in wire["points"]]
    ys = [y for wire in drawing.wires for _, y in wire["points"]]
    for label in drawing.labels:
        xs.extend([label["box"][0], label["box"][2]])
        ys.extend([label["box"][1], label["box"][3]])
    for symbol in drawing.symbols:
        for point in symbol.get("points", []):
            xs.append(point[0])
            ys.append(point[1])
    left, top = min(xs) - MARGIN, min(ys) - MARGIN
    for wire in drawing.wires:
        wire["points"] = [(x - left, y - top) for x, y in wire["points"]]
    drawing.dots = [(x - left, y - top) for x, y in drawing.dots]
    drawing.grounds = [(x - left, y - top) for x, y in drawing.grounds]
    drawing.terminals = {name: [(x - left, y - top) for x, y in points]
                         for name, points in drawing.terminals.items()}
    for label in drawing.labels:
        label["at"] = (label["at"][0] - left, label["at"][1] - top)
        label["box"] = (label["box"][0] - left, label["box"][1] - top,
                        label["box"][2] - left, label["box"][3] - top)
    for symbol in drawing.symbols:
        if "points" in symbol:
            symbol["points"] = [(x - left, y - top) for x, y in symbol["points"]]
        for key in ("from", "to", "centre"):
            if key in symbol:
                symbol[key] = (symbol[key][0] - left, symbol[key][1] - top)
    drawing.width = max(x for wire in drawing.wires for x, _ in wire["points"]) + MARGIN
    drawing.height = max(y for wire in drawing.wires for _, y in wire["points"]) + MARGIN
    drawing.width = max(drawing.width, max(label["box"][2] for label in drawing.labels) + 8)
    drawing.height = max(drawing.height, max(label["box"][3] for label in drawing.labels) + 8)


# --- reading the drawing back out ---------------------------------------------

def _segments(drawing: Drawing) -> list[tuple[Point, Point]]:
    parts: list[tuple[Point, Point]] = []
    for wire in drawing.wires:
        points = wire["points"]
        parts.extend(zip(points, points[1:]))
    return parts


def _on(segment: tuple[Point, Point], point: Point) -> bool:
    (x1, y1), (x2, y2) = segment
    if abs(x1 - x2) < 1e-6:
        return abs(point[0] - x1) < 1e-6 and min(y1, y2) - 1e-6 <= point[1] <= max(y1, y2) + 1e-6
    if abs(y1 - y2) < 1e-6:
        return abs(point[1] - y1) < 1e-6 and min(x1, x2) - 1e-6 <= point[0] <= max(x1, x2) + 1e-6
    return False


def connections(drawing: Drawing, strict: bool = False) -> dict[str, tuple[str, ...]]:
    """What the geometry says each element is wired to, reading only the drawing.

    Two wires join where they share an end, where a dot marks the junction, and
    where they run over one another. A wire crossing another with no dot joins
    nothing, which is what lets a difference amplifier be drawn at all.
    """
    groups: dict[Point, Point] = {}

    def find(point: Point) -> Point:
        groups.setdefault(point, point)
        while groups[point] != point:
            groups[point] = groups[groups[point]]
            point = groups[point]
        return point

    def union(first: Point, second: Point) -> None:
        a, b = find(first), find(second)
        if a != b:
            groups[a] = b

    segments = _segments(drawing)
    for start, end in segments:
        union(start, end)
    joints = set(drawing.dots) | {point for points in drawing.terminals.values() for point in points}
    joints |= set(drawing.grounds)
    for point in joints:
        for segment in segments:
            if _on(segment, point):
                union(point, segment[0])
    for first in segments:
        for second in segments:
            if first is second:
                continue
            # Collinear overlap joins; a crossing does not.
            if _on(first, second[0]) and _on(first, second[1]):
                union(first[0], second[0])
    for point in drawing.grounds:
        union(point, drawing.grounds[0])

    wired: dict[str, tuple[str, ...]] = {}
    for name, points in drawing.terminals.items():
        wired[name] = tuple(f"g{hash(find(point)) & 0xffff:04x}" for point in points)
    if strict:
        _refuse_mismatch(drawing, wired)
    return wired


def _incidence(wired: dict[str, tuple[str, ...]]) -> set[frozenset[tuple[str, int]]]:
    """Which element terminals share a node, with the node names thrown away."""
    nodes: dict[str, set[tuple[str, int]]] = {}
    for name, groups in wired.items():
        for index, group in enumerate(groups):
            nodes.setdefault(group, set()).add((name, index))
    return {frozenset(members) for members in nodes.values()}


def netlist_of(source: "Drawing | str") -> set[frozenset[tuple[str, int]]]:
    """The connections a netlist or a drawing describes, as one comparable thing."""
    if isinstance(source, Drawing):
        return _incidence(connections(source))
    wired: dict[str, tuple[str, ...]] = {}
    for element in parse(source):
        if element.kind == "opamp":
            out, _, plus, minus = element.nodes
            wired[element.name] = (out, minus, plus)
        else:
            wired[element.name] = element.nodes
    return _incidence(wired)


def _refuse_mismatch(drawing: Drawing, wired: dict[str, tuple[str, ...]]) -> None:
    declared = _incidence({
        element.name: ((element.nodes[0], element.nodes[3], element.nodes[2])
                       if element.kind == "opamp" else element.nodes)
        for element in drawing.elements
    })
    if _incidence(wired) != declared:
        raise SchematicError(
            "the drawing no longer describes its netlist: the wires join "
            f"{sorted(sorted(group) for group in _incidence(wired))}, "
            f"the netlist joins {sorted(sorted(group) for group in declared)}"
        )


def verify(drawing: Drawing) -> None:
    """Refuse a drawing whose geometry says something other than its netlist."""
    connections(drawing, strict=True)


# --- drawing it ---------------------------------------------------------------

def _n(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _symbol(symbol: dict[str, Any]) -> str:
    if symbol["shape"] == "opamp":
        points = " ".join(f"{_n(x)},{_n(y)}" for x, y in symbol["points"])
        return f'<polygon points="{points}" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    if symbol["shape"] == "source":
        x, y = symbol["centre"]
        mark = "~" if symbol["kind"] == "V" else "↑"
        return (f'<circle cx="{_n(x)}" cy="{_n(y)}" r="16" fill="none" stroke="currentColor" stroke-width="1.6"/>'
                f'<text x="{_n(x)}" y="{_n(y + 4)}" text-anchor="middle" font-size="12" fill="currentColor">{mark}</text>')
    (x1, y1), (x2, y2) = symbol["from"], symbol["to"]
    if symbol["shape"] == "R":
        if symbol["vertical"]:
            step = (y2 - y1) / 6
            zigzag = " ".join(f"{_n(x1 + (8 if i % 2 else -8))},{_n(y1 + step * i)}" for i in range(1, 6))
            return (f'<polyline points="{_n(x1)},{_n(y1)} {zigzag} {_n(x2)},{_n(y2)}" '
                    f'fill="none" stroke="currentColor" stroke-width="1.6"/>')
        step = (x2 - x1) / 6
        zigzag = " ".join(f"{_n(x1 + step * i)},{_n(y1 + (8 if i % 2 else -8))}" for i in range(1, 6))
        return (f'<polyline points="{_n(x1)},{_n(y1)} {zigzag} {_n(x2)},{_n(y2)}" '
                f'fill="none" stroke="currentColor" stroke-width="1.6"/>')
    # Capacitor or inductor: two plates, or a coil drawn as a thicker bar.
    if symbol["vertical"]:
        mid = (y1 + y2) / 2
        gap = 5 if symbol["shape"] == "C" else 0
        return (f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x1)}" y2="{_n(mid - gap)}" stroke="currentColor" stroke-width="1.6"/>'
                f'<line x1="{_n(x1 - 12)}" y1="{_n(mid - gap)}" x2="{_n(x1 + 12)}" y2="{_n(mid - gap)}" stroke="currentColor" stroke-width="2"/>'
                f'<line x1="{_n(x1 - 12)}" y1="{_n(mid + gap)}" x2="{_n(x1 + 12)}" y2="{_n(mid + gap)}" stroke="currentColor" stroke-width="2"/>'
                f'<line x1="{_n(x1)}" y1="{_n(mid + gap)}" x2="{_n(x1)}" y2="{_n(y2)}" stroke="currentColor" stroke-width="1.6"/>')
    mid = (x1 + x2) / 2
    gap = 5 if symbol["shape"] == "C" else 0
    return (f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(mid - gap)}" y2="{_n(y1)}" stroke="currentColor" stroke-width="1.6"/>'
            f'<line x1="{_n(mid - gap)}" y1="{_n(y1 - 12)}" x2="{_n(mid - gap)}" y2="{_n(y1 + 12)}" stroke="currentColor" stroke-width="2"/>'
            f'<line x1="{_n(mid + gap)}" y1="{_n(y1 - 12)}" x2="{_n(mid + gap)}" y2="{_n(y1 + 12)}" stroke="currentColor" stroke-width="2"/>'
            f'<line x1="{_n(mid + gap)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" stroke="currentColor" stroke-width="1.6"/>')


def svg(drawing: Drawing) -> str:
    """One SVG element, every text escaped, no script anywhere."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(drawing.width)} {_n(drawing.height)}" '
        f'width="{_n(drawing.width)}" height="{_n(drawing.height)}" role="img">'
    ]
    for wire in drawing.wires:
        points = " ".join(f"{_n(x)},{_n(y)}" for x, y in wire["points"])
        parts.append(f'<polyline points="{points}" fill="none" stroke="currentColor" stroke-width="1.4"/>')
    for symbol in drawing.symbols:
        parts.append(_symbol(symbol))
    for x, y in drawing.dots:
        parts.append(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="3" fill="currentColor"/>')
    for x, y in drawing.grounds:
        parts.append(
            f'<line x1="{_n(x)}" y1="{_n(y - 10)}" x2="{_n(x)}" y2="{_n(y)}" stroke="currentColor" stroke-width="1.4"/>'
            f'<line x1="{_n(x - 10)}" y1="{_n(y)}" x2="{_n(x + 10)}" y2="{_n(y)}" stroke="currentColor" stroke-width="1.6"/>'
            f'<line x1="{_n(x - 6)}" y1="{_n(y + 4)}" x2="{_n(x + 6)}" y2="{_n(y + 4)}" stroke="currentColor" stroke-width="1.4"/>'
            f'<line x1="{_n(x - 2)}" y1="{_n(y + 8)}" x2="{_n(x + 2)}" y2="{_n(y + 8)}" stroke="currentColor" stroke-width="1.4"/>'
        )
    for label in drawing.labels:
        x, y = label["at"]
        parts.append(
            f'<text x="{_n(x)}" y="{_n(y + label["size"] / 3)}" text-anchor="middle" '
            f'font-size="{_n(label["size"])}" font-family="ui-monospace, monospace" '
            f'fill="currentColor">{escape(label["text"])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
