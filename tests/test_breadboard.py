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


@pytest.mark.parametrize("field", ["chips", "parts", "opamps", "sources", "probes"])
def test_a_list_field_that_is_not_a_list_is_refused_by_name(field):
    with pytest.raises(BuildError, match=f"{field} must be a list"):
        parse_build(spec(**{field: 5}))


def test_pot_positions_that_is_not_an_object_is_refused():
    with pytest.raises(BuildError, match="pot_positions must be an object"):
        parse_build(spec(pot_positions=["R1"]))


def test_more_than_max_parts_is_refused():
    from circuit_mcp.breadboard import MAX_PARTS
    parts = [{"ref": f"R{i}", "kind": "resistor", "value": "1k", "nodes": [f"a{i}", f"b{i}"]} for i in range(MAX_PARTS + 1)]
    with pytest.raises(BuildError, match=f"at most {MAX_PARTS} parts"):
        parse_build(spec(parts=parts))


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


def test_every_two_terminal_part_lands_on_distinct_real_holes():
    layout = place(parse_build(lab1.EXP6_DIFFERENCE))
    for part in layout.placed:
        assert len(part["ends"]) == len(part["nodes"])
        assert len(set(part["ends"])) == len(part["ends"])
        for end in part["ends"]:
            if end[0] == "rail":
                assert 1 <= end[2] <= 30
            else:
                assert end[0] in ("top", "bottom")
                assert 1 <= end[1] <= 30
                assert end[2] in "abcdefghij"


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


CROSSED_RAIL = {"top+": "top-", "bot-": "bot+"}   # the near rail a lead to the far rail passes over


def _body(part):
    """Every hole a placed part's body lies over, not just its ends."""
    ends = part["ends"]
    rails = [e for e in ends if e[0] == "rail"]
    if len(rails) == 1 and len(ends) == 2:
        rail = rails[0]
        strip = ends[0] if ends[1] == rail else ends[1]
        assert rail[2] == strip[1], f"{part['ref']} runs diagonally from {strip} to {rail}"
        side, col, row = strip
        rows = "abcde" if side == "top" else "fghij"
        covered = rows[:rows.index(row) + 1] if side == "top" else rows[rows.index(row):]
        body = {(side, col, r) for r in covered} | {rail}
        crossed = CROSSED_RAIL.get(rail[1])
        if crossed is not None:
            body.add(("rail", crossed, col))
        return body
    (s1, c1, r1), (s2, c2, r2) = ends[0], ends[-1]
    if s1 == s2 and r1 == r2:
        return {(s1, c, r1) for c in range(min(c1, c2), max(c1, c2) + 1)}
    if s1 != s2 and c1 == c2 and {r1, r2} == {"a", "j"}:
        return {("top", c1, r) for r in "abcde"} | {("bottom", c1, r) for r in "fghij"}
    return set(ends)


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_nothing_is_placed_under_a_component_body(name):
    layout = place(parse_build(lab1.ALL[name]))
    bodies = {part["ref"]: _body(part) for part in layout.placed}
    for ref, body in bodies.items():
        for other, other_body in bodies.items():
            if other != ref:
                assert not (body & other_body), f"{ref} and {other} overlap at {body & other_body}"
        for jumper in layout.jumpers:
            for end in jumper["ends"]:
                assert end not in body, f"jumper for {jumper['net']} at {end} is under {ref}"
        for item in layout.attachments:
            assert item["hole"] not in body, f"{item['label']} at {item['hole']} is under {ref}"


def test_verify_refuses_a_part_missing_an_end():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    layout.placed[0]["ends"] = layout.placed[0]["ends"][:-1]
    with pytest.raises(BuildError, match="ends for"):
        verify(layout)


def test_a_multi_pin_part_refuses_an_occupied_hole():
    from circuit_mcp.breadboard import Layout, Part, _place_multi_pin
    layout = Layout(parse_build(lab1.EXP1_NONINVERTING))
    layout.used.add(("top", 20, "c"))
    with pytest.raises(BuildError, match="already occupied"):
        _place_multi_pin(layout, Part("D9", "led", "", ("x", "y")))


def test_the_board_runs_out_of_columns_loudly():
    parts = [{"ref": f"R{i}", "kind": "resistor", "value": "1k", "nodes": [f"a{i}", f"b{i}"]} for i in range(12)]
    with pytest.raises(BuildError, match="out of free columns"):
        place(parse_build({"supply": {"vplus": 5, "vminus": 0}, "parts": parts}))


def test_a_strip_to_rail_part_runs_straight_or_is_rerouted():
    for name in sorted(lab1.ALL):
        layout = place(parse_build(lab1.ALL[name]))
        for part in layout.placed:
            ends = part["ends"]
            rails = [e for e in ends if e[0] == "rail"]
            strips = [e for e in ends if e[0] != "rail"]
            if len(rails) == 1 and len(strips) == 1:
                assert rails[0][2] == strips[0][1], f"{name} {part['ref']} runs diagonally: {ends}"
                assert strips[0][2] in ("a", "j"), f"{name} {part['ref']} does not start at the outer row: {ends}"


def test_free_hole_on_a_rail_prefers_the_column_nearest_its_other_end_then_the_lower_one_on_a_tie():
    from circuit_mcp.breadboard import Layout, _free_hole
    layout = Layout(parse_build(lab1.EXP1_NONINVERTING))
    assert _free_hole(layout, ("rail", "bot+"), near=14) == ("rail", "bot+", 14)
    assert _free_hole(layout, ("rail", "bot+"), near=14) == ("rail", "bot+", 13)


# --- output --------------------------------------------------------------------

import re  # noqa: E402

import math  # noqa: E402

from circuit_mcp.breadboard_view import draw, hole_name, is_rail_bridge, layout_payload, nets, svg, wire_list  # noqa: E402


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
    from xml.etree import ElementTree
    hostile = copy.deepcopy(lab1.EXP1_NONINVERTING)
    hostile["probes"] = [{"label": "<script>&\"'x", "node": "out"}]
    picture = svg(place(parse_build(hostile)))
    assert "<script>" not in picture
    assert "&lt;script&gt;" in picture
    ElementTree.fromstring(picture)


def test_svg_draws_the_chip_rails_and_probes():
    picture = svg(place(parse_build(lab1.EXP1_NONINVERTING)))
    assert picture.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "U1 LM324" in picture
    assert ">V+<" in picture and ">V-<" in picture and picture.count(">GND<") == 2
    assert "CH2 vo" in picture


def test_layout_payload_has_what_the_card_renders():
    payload = layout_payload(lab1.EXP4B_BUFFERED)
    assert set(payload) == {"svg", "wires", "holes", "probes", "nets"}
    assert all(re.match(r"^([a-j]\d+|(top|bot)[+-] rail \(col \d+\))$", h) for holes in payload["holes"].values() for h in holes)
    assert [p["label"] for p in payload["probes"]] == ["VA", "VB"]


def test_chip_pin_numbers_are_drawn_on_the_chip_body():
    picture = svg(place(parse_build(lab1.EXP1_NONINVERTING)))
    body = re.search(r'<rect class="chip-body" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', picture)
    assert body, "chip body rect not found"
    x1, y1, w, h = (float(v) for v in body.groups())
    labels = re.findall(r'<text class="pin-number" x="([\d.]+)" y="([\d.]+)"[^>]*>(\d+)</text>', picture)
    assert sorted(int(n) for _, _, n in labels) == list(range(1, 15))
    for x, y, n in labels:
        assert x1 <= float(x) <= x1 + w and y1 + 6 <= float(y) <= y1 + h - 2, f"pin {n} label at ({x}, {y}) is off the body"


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_every_lab1_svg_is_well_formed_xml(name):
    from xml.etree import ElementTree
    ElementTree.fromstring(svg(place(parse_build(lab1.ALL[name]))))


def test_the_ground_rail_bridge_is_instructed_once():
    wires = wire_list(place(parse_build(lab1.EXP1_NONINVERTING)))
    assert sum("jumper the two blue rails together" in w for w in wires) == 1
    assert not [w for w in wires if w.startswith("Jumper") and "top- rail" in w and "bot- rail" in w]


def test_the_chip_step_names_the_chips_own_power_pins():
    from circuit_mcp import parts as P
    chip = P.chip("LM324")
    step = next(w for w in wire_list(place(parse_build(lab1.EXP1_NONINVERTING))) if w.startswith("U1 LM324"))
    assert f"pin {chip.vplus} (V+)" in step and f"pin {chip.vminus} (V-)" in step


def test_the_drawing_labels_parts_with_the_same_unit_as_the_wire_list():
    picture = svg(place(parse_build(lab1.EXP1_NONINVERTING)))
    assert ">R1 1kΩ<" in picture


# --- the contract the review found holes in -----------------------------------

@pytest.mark.parametrize("field", ["opamp", "pot_position"])
def test_an_unknown_top_level_field_is_refused_by_name(field):
    """A misspelled key used to be dropped: 'opamp' drew an unwired chip, and
    'pot_position' quietly reset every pot to the middle."""
    with pytest.raises(BuildError, match="unknown build field"):
        parse_build({**MINIMAL, field: []})


def test_a_two_terminal_part_with_both_leads_on_one_node_is_refused():
    with pytest.raises(BuildError, match="R9: both leads are on a"):
        parse_build(spec(parts=MINIMAL["parts"] + [
            {"ref": "R9", "kind": "resistor", "value": "1k", "nodes": ["a", "a"]}]))


def test_more_than_max_probes_is_refused():
    from circuit_mcp.breadboard import MAX_PROBES
    probes = [{"label": f"CH{i}", "node": "out"} for i in range(MAX_PROBES + 1)]
    with pytest.raises(BuildError, match=f"at most {MAX_PROBES} probes"):
        parse_build(spec(probes=probes))


def test_a_pot_may_wire_two_of_its_pins_to_the_same_net():
    """Exp 3 ties the wiper to the far end; that is a real knob, not a shorted part."""
    layout = place(parse_build(lab1.EXP3_INVERTING))
    pot = next(p for p in layout.placed if p["ref"] == "R2")
    assert pot["nodes"] == ["inn", "out", "out"]
    assert len(set(pot["ends"])) == 3


def test_verify_refuses_a_part_with_two_leads_in_one_hole():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    part = next(p for p in layout.placed if p["ref"] == "R1")
    part["ends"] = [part["ends"][0], part["ends"][0]]
    with pytest.raises(BuildError, match="R1 has two leads in one hole"):
        verify(layout)


def test_the_wire_list_says_where_to_set_each_pot():
    wires = wire_list(place(parse_build(lab1.EXP1_NONINVERTING)))
    step = next(w for w in wires if w.startswith("R3 10kΩ pot"))
    assert "Set to 50% of travel from the first end." in step


# --- a drawing a student can read ----------------------------------------------

def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _used_holes(layout):
    """Every hole something is plugged into, read from the layout rather than the drawing."""
    holes = [hole for part in layout.placed for hole in part["ends"]]
    holes += [hole for jumper in layout.jumpers if not is_rail_bridge(jumper) for hole in jumper["ends"]]
    holes += [item["hole"] for item in layout.attachments]
    return holes


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_labels_never_overlap_each_other_a_used_hole_or_the_chip_and_stay_on_the_board(name):
    layout = place(parse_build(lab1.ALL[name]))
    drawing = draw(layout)
    assert drawing.labels, "every lab circuit labels at least one part"
    chip = None
    if layout.chip:
        points = [drawing.xy(hole) for hole in layout.chip["pins"].values()]
        chip = (min(x for x, _ in points) - 9, min(y for _, y in points) - 9,
                max(x for x, _ in points) + 9, max(y for _, y in points) + 9)
    for i, box in enumerate(drawing.labels):
        assert 0 <= box[0] and box[2] <= drawing.width and 0 <= box[1] and box[3] <= drawing.height, f"{box} is off the board"
        for other in drawing.labels[i + 1:]:
            assert not _overlap(box, other), f"labels {box} and {other} overlap"
        for hole in _used_holes(layout):
            x, y = drawing.xy(hole)
            assert not _overlap(box, (x - 2.3, y - 2.3, x + 2.3, y + 2.3)), f"label {box} covers {hole_name(hole)}"
        if chip:
            assert not _overlap(box, chip), f"label {box} covers the chip"


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_every_wire_list_step_is_numbered_on_the_board_and_nothing_else_is(name):
    payload = layout_payload(lab1.ALL[name])
    drawn = {int(n) for n in re.findall(r'data-step="(\d+)"', payload["svg"])}
    assert drawn == set(range(1, len(payload["wires"]) + 1))


def test_every_net_wears_one_colour_on_its_wires_its_strips_and_the_legend():
    layout = place(parse_build(lab1.EXP8_INSTRUMENTATION))
    listed = {n["net"]: n for n in nets(layout)}
    assert (listed["gnd"]["color_name"], listed["vplus"]["color_name"], listed["vminus"]["color_name"]) == ("black", "red", "blue")
    signal = [n for key, n in listed.items() if key not in ("gnd", "vplus", "vminus")]
    assert len({n["color"] for n in signal}) == len(signal), "two signal nets share a colour"
    picture = svg(layout)
    for jumper in layout.jumpers:
        net, colour = jumper["net"], listed[jumper["net"]]["color"]
        assert re.search(rf'<g class="jumper" data-step="\d+" data-nets="{net}"><path[^>]*/><path class="wire" d="[^"]*" stroke="{colour}"', picture), jumper
    for net in listed:
        assert f'class="strip" data-nets="{net}"' in picture, f"no strip is tinted for {net}"


def test_wires_arc_instead_of_lying_flat_over_the_holes_between_their_ends():
    picture = svg(place(parse_build(lab1.EXP8_INSTRUMENTATION)))
    wires = re.findall(r'<path class="wire" d="M ([-\d.]+) ([-\d.]+) Q ([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"', picture)
    assert wires
    for ax, ay, cx, cy, bx, by in ([float(v) for v in wire] for wire in wires):
        bulge = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / math.hypot(bx - ax, by - ay)
        assert bulge >= 7, f"a wire from ({ax}, {ay}) to ({bx}, {by}) lies flat"


def test_the_board_is_cropped_to_the_columns_in_use():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    drawing = draw(layout)
    used = {hole[1] for part in layout.placed for hole in part["ends"] if hole[0] != "rail"}
    assert drawing.first_col <= min(used) and max(used) <= drawing.last_col
    assert drawing.last_col - drawing.first_col + 1 < 30
    assert ">30<" not in drawing.svg


def test_chip_pins_say_what_they_do():
    picture = svg(place(parse_build(lab1.EXP1_NONINVERTING)))
    for pin, label in ((1, "OUT A"), (2, "IN- A"), (3, "IN+ A"), (4, "V+"), (11, "V-"), (7, "OUT B")):
        assert re.search(rf'<text class="pin-function" data-pin="{pin}"[^>]*>{re.escape(label)}</text>', picture), pin


def test_the_legend_says_what_each_net_joins():
    listed = {n["net"]: n for n in nets(place(parse_build(lab1.EXP1_NONINVERTING)))}
    assert listed["vi"]["members"] == ["U1 pin 3 (IN+ A)", "R3 wiper", "CH1 vi"]
    assert listed["out"]["members"] == ["U1 pin 1 (OUT A)", "R2", "CH2 vo"]
    assert listed["vplus"]["name"] == "V+" and listed["vplus"]["members"][:2] == ["bottom red rail (+15 V)", "U1 pin 4 (V+)"]
    assert listed["gnd"]["name"] == "GND" and "R1" in listed["gnd"]["members"] and "R3 end" in listed["gnd"]["members"]


def test_jumper_steps_name_the_wire_colour_the_drawing_uses():
    layout = place(parse_build(lab1.EXP1_NONINVERTING))
    colours = {n["net"]: n["color_name"] for n in nets(layout)}
    jumpers = [step for step in wire_list(layout) if step.startswith("Jumper")]
    assert jumpers
    for step in jumpers:
        named = re.match(r"Jumper, (\w+) \((\w+)\): ", step)
        assert named and colours[named.group(2)] == named.group(1), step


def test_the_pot_shows_its_setting_on_the_board():
    assert ">R3 10kΩ · 50%<" in svg(place(parse_build(lab1.EXP1_NONINVERTING)))


def test_probes_take_scope_channel_colours_in_order():
    from circuit_mcp.breadboard_view import PROBE_COLOURS
    picture = svg(place(parse_build(lab1.EXP1_NONINVERTING)))
    first = re.search(r'<g class="probe" data-step="\d+" data-nets="vi"><circle[^>]*stroke="([^"]+)"', picture)
    second = re.search(r'<g class="probe" data-step="\d+" data-nets="out"><circle[^>]*stroke="([^"]+)"', picture)
    assert (first.group(1), second.group(1)) == PROBE_COLOURS[:2]


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_ground_connects_to_the_blue_rail_on_its_own_side(name):
    """Both blue rails are ground; a wire to the far one crosses the whole board, often through the chip."""
    layout = place(parse_build(lab1.ALL[name]))
    links = [jumper["ends"] for jumper in layout.jumpers if jumper["net"] == GROUND] + [part["ends"] for part in layout.placed]
    for ends in links:
        rails = [end for end in ends if end[0] == "rail" and end[1] in ("top-", "bot-")]
        strips = [end for end in ends if end[0] != "rail"]
        if len(rails) == 1 and len(strips) == 1:
            nearest = "top-" if strips[0][0] == "top" else "bot-"
            assert rails[0][1] == nearest, f"{name}: {[hole_name(end) for end in ends]} runs to the far ground rail"


RAIL_NETS = {GROUND, "vplus", "vminus"}


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_a_part_reaches_no_further_than_a_bent_lead_does(name):
    """A quarter-watt resistor spans a few holes; one stretched across the board hides everything under it."""
    layout = place(parse_build(lab1.ALL[name]))
    for part in layout.placed:
        cols = [end[2] if end[0] == "rail" else end[1] for end in part["ends"]]
        assert max(cols) - min(cols) <= 4, f"{name} {part['ref']} spans {[hole_name(end) for end in part['ends']]}"


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_a_part_to_a_rail_plugs_into_the_rail_itself(name):
    """A detour through a spare column adds a wire and a long lead the student has to trace."""
    layout = place(parse_build(lab1.ALL[name]))
    for part in layout.placed:
        if part["kind"] not in ("pot", "led") and len(RAIL_NETS & set(part["nodes"])) == 1:
            assert any(end[0] == "rail" for end in part["ends"]), f"{name} {part['ref']} detours: {[hole_name(end) for end in part['ends']]}"


def _path_points(d, samples=24):
    """Points along an absolute M/L/Q/C SVG path."""
    tokens = re.findall(r"[MLQC]|-?[\d.]+", d)
    points, pen, i = [], None, 0
    while i < len(tokens):
        command = tokens[i]
        count = {"M": 1, "L": 1, "Q": 2, "C": 3}[command]
        coords = [(float(tokens[i + 1 + 2 * k]), float(tokens[i + 2 + 2 * k])) for k in range(count)]
        i += 1 + 2 * count
        if command == "M":
            pen = coords[0]
            points.append(pen)
            continue
        control = [pen, *coords]
        for step in range(1, samples + 1):
            t, row = step / samples, list(control)
            while len(row) > 1:
                row = [((1 - t) * p[0] + t * q[0], (1 - t) * p[1] + t * q[1]) for p, q in zip(row, row[1:])]
            points.append(row[0])
        pen = coords[-1]
    return points


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_the_ground_bridge_runs_in_the_margin_not_over_the_board(name):
    drawing = draw(place(parse_build(lab1.ALL[name])))
    power = re.search(r'<g class="power".*?</g></g>', drawing.svg).group(0)
    bridge = re.search(r'<path class="wire" d="([^"]+)"', power).group(1)
    top, bottom = drawing.y("rail-top-"), drawing.y("rail-bot-")
    margin = drawing.x(drawing.last_col) + 12
    for x, y in _path_points(bridge):
        if top + 7 < y < bottom - 7:
            assert x >= margin, f"the bridge crosses the board at ({x:.1f}, {y:.1f})"


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_no_wire_passes_over_a_probe_or_source_it_does_not_end_at(name):
    picture = svg(place(parse_build(lab1.ALL[name])))
    markers = [(float(x), float(y)) for x, y in re.findall(r'<g class="(?:probe|source)"[^>]*><circle cx="([-\d.]+)" cy="([-\d.]+)"', picture)]
    assert markers
    for d in re.findall(r'<path class="wire" d="([^"]+)"', picture):
        points = _path_points(d, samples=48)
        ends = (points[0], points[-1])
        for marker in markers:
            if any(math.dist(marker, end) < 1 for end in ends):
                continue
            nearest = min(math.dist(marker, point) for point in points)
            assert nearest >= 7.5, f"a wire passes {nearest:.1f} from the marker at {marker}"


def _crosses(p, q):
    """Whether two polylines intersect anywhere but their shared ends."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    for a, b in zip(p, p[1:]):
        for c, d in zip(q, q[1:]):
            if ccw(a, b, c) * ccw(a, b, d) < 0 and ccw(c, d, a) * ccw(c, d, b) < 0:
                return True
    return False


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_a_wire_nested_inside_another_does_not_cross_it(name):
    """Two wires on one side, one spanning inside the other, can always bow without crossing."""
    picture = svg(place(parse_build(lab1.ALL[name])))
    wires = [_path_points(d, samples=48) for d in re.findall(r'<path class="wire" d="(M [^"]* Q [^"]*)"', picture)]
    for i, p in enumerate(wires):
        for q in wires[i + 1:]:
            (p0, p1), (q0, q1) = sorted((p[0][0], p[-1][0])), sorted((q[0][0], q[-1][0]))
            nested = (p0 <= q0 and q1 <= p1) or (q0 <= p0 and p1 <= q1)
            same_side = all(abs(end[1] - p[0][1]) < 1 for end in (p[-1], q[0], q[-1]))
            if nested and same_side:
                assert not _crosses(p, q), f"{name}: wires from {p[0]} and {q[0]} cross"


def test_a_wire_bows_the_way_that_does_not_cross_a_wire_already_drawn():
    from circuit_mcp.breadboard_view import _arc, _bezier
    drawn = [_bezier((0, 100), (50, 130), (100, 100), i / 24) for i in range(25)]   # bows down
    a, b = (20, 100), (140, 100)
    control = _arc(a, b, solids=[], bounds=(-50, 0, 250, 300), markers=[], holes=[], middle=250, drawn=[drawn])
    new = [_bezier(a, control, b, i / 48) for i in range(49)]
    assert not _crosses(drawn, new), f"bowed through {control} across the drawn wire"


def _net_strips(layout):
    """Every strip each net occupies once the build is placed."""
    owners = {}
    for part in layout.placed:
        for net, hole in zip(part["nodes"], part["ends"]):
            owners.setdefault(net, set()).add(strip_of(hole))
    for jumper in layout.jumpers:
        for hole in jumper["ends"]:
            owners.setdefault(jumper["net"], set()).add(strip_of(hole))
    for strip, net in layout.strip_net.items():
        owners.setdefault(net, set()).add(strip)
    return owners


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_a_wire_never_passes_a_strip_on_its_own_net_to_reach_a_farther_one(name):
    """Each wire joins its net at the nearest strip already on it, so wires chain instead of fanning out."""
    layout = place(parse_build(lab1.ALL[name]))
    owners = _net_strips(layout)
    for jumper in layout.jumpers:
        (s1, c1, _), (s2, c2, _) = jumper["ends"]
        if s1 == s2 and s1 != "rail":
            lo, hi = sorted((c1, c2))
            between = sorted(col for side, col in owners[jumper["net"]] if side == s1 and lo < col < hi)
            assert not between, f"{name}: the {jumper['net']} wire {[hole_name(e) for e in jumper['ends']]} passes columns {between} already on {jumper['net']}"


@pytest.mark.parametrize("name", sorted(lab1.ALL))
def test_a_wire_between_strips_on_one_side_stays_in_one_row(name):
    """A wire that changes rows bends around whatever sits between its ends."""
    layout = place(parse_build(lab1.ALL[name]))
    for jumper in layout.jumpers:
        (s1, _, r1), (s2, _, r2) = jumper["ends"]
        if s1 == s2 and s1 != "rail":
            assert r1 == r2, f"{name}: the {jumper['net']} wire {[hole_name(e) for e in jumper['ends']]} changes rows"


def test_a_build_without_a_chip_does_not_leave_room_for_one():
    """With no chip in the middle, the parts gather there instead of splitting to either side of an empty gap."""
    layout = place(parse_build(lab1.EXP4A_DIVIDER_LED))
    cols = [hole[2] if hole[0] == "rail" else hole[1] for hole in _used_holes(layout)]
    assert max(cols) - min(cols) <= 8, f"the build spreads across columns {min(cols)} to {max(cols)}"


# Builds a random search found where a part crossing the trench landed on a strip
# a spare column had already given to another net.
CROWDED = [
    {"supply": {"vplus": 15, "vminus": 0}, "chips": [{"ref": "U1", "part": "LM324"}],
     "opamps": [{"ref": "U1A", "chip": "U1", "section": "a", "inp": "n2", "inn": "n3", "out": "n3"}],
     "parts": [{"ref": "X0", "kind": "resistor", "nodes": ["gnd", "n5"], "value": "1k"},
               {"ref": "X1", "kind": "resistor", "nodes": ["n3", "n4"], "value": "1k"},
               {"ref": "X2", "kind": "resistor", "nodes": ["n5", "gnd"], "value": "1k"},
               {"ref": "X3", "kind": "resistor", "nodes": ["n1", "n0"], "value": "1k"},
               {"ref": "X4", "kind": "resistor", "nodes": ["n2", "n4"], "value": "1k"},
               {"ref": "X5", "kind": "led", "nodes": ["n4", "n3"]},
               {"ref": "X6", "kind": "resistor", "nodes": ["n2", "n1"], "value": "1k"},
               {"ref": "X7", "kind": "resistor", "nodes": ["n4", "n5"], "value": "1k"}],
     "sources": [{"ref": "VS", "kind": "dc", "node": "n1", "volts": 1}],
     "probes": [{"label": "CH1", "node": "n4", "role": "output"}]},
    {"supply": {"vplus": 5, "vminus": -15}, "chips": [{"ref": "U1", "part": "LMC660"}],
     "opamps": [{"ref": "U1B", "chip": "U1", "section": "b", "inp": "n2", "inn": "n0", "out": "n3"},
                {"ref": "U1A", "chip": "U1", "section": "a", "inp": "gnd", "inn": "n4", "out": "n3"}],
     "parts": [{"ref": "X0", "kind": "resistor", "nodes": ["n3", "gnd"], "value": "1k"},
               {"ref": "X1", "kind": "resistor", "nodes": ["gnd", "n3"], "value": "1k"},
               {"ref": "X2", "kind": "resistor", "nodes": ["gnd", "n2"], "value": "1k"},
               {"ref": "X3", "kind": "resistor", "nodes": ["n4", "n3"], "value": "1k"},
               {"ref": "X4", "kind": "capacitor", "nodes": ["n4", "n2"], "value": "10n"},
               {"ref": "X5", "kind": "capacitor", "nodes": ["n1", "n2"], "value": "10n"}],
     "sources": [{"ref": "VS", "kind": "dc", "node": "n0", "volts": 1}],
     "probes": [{"label": "CH1", "node": "n3", "role": "output"}, {"label": "CH2", "node": "n4", "role": "output"}]},
]


@pytest.mark.parametrize("build", CROWDED, ids=["cross-trench-onto-a-spare", "cross-trench-onto-a-rail-route"])
def test_a_part_never_lands_on_a_strip_another_net_already_holds(build):
    place(parse_build(build))   # verify() inside place refuses a short loudly
