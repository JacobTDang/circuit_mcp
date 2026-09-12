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
