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
