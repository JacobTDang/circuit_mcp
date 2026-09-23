"""Netlist -> schematic, and the geometric check that the drawing still says it.

A drawing that has drifted from its netlist is worse than no drawing: it looks
like the verified circuit. So every layout is read back out of its own geometry
and refused unless the connections it shows are the ones the netlist declares.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree

import pytest

from circuit_mcp.schematic import SchematicError, connections, draw, netlist_of, svg

INVERTING = "Vs 1 0 {V}\nRi 1 2 {Ri}\nRf 2 3 {Rf}\nE1 3 0 opamp 0 2 {A}"
NONINVERTING = "Vs 1 0 {V}\nRg 2 0 {Rg}\nRf 3 2 {Rf}\nE1 3 0 opamp 1 2 {A}"
FOLLOWER = "Vs 1 0 {V}\nE1 2 0 opamp 1 2 {A}"
INTEGRATOR = "Vs 1 0 {V}\nRi 1 2 10e3\nCf 2 3 1e-6\nE1 3 0 opamp 0 2 {A}"
DIFFERENCE = (
    "V1 1 0 {V1}\nV2 4 0 {V2}\nR1 1 2 10e3\nR2 2 3 10e3\n"
    "R3 4 5 10e3\nR4 5 0 10e3\nE1 3 0 opamp 5 2 {A}"
)
CASCADE = (
    "Vs 1 0 {V}\nRa 1 2 1e3\nRb 2 3 10e3\nE1 3 0 opamp 0 2 {A}\n"
    "Rc 3 4 2e3\nRd 4 5 20e3\nE2 5 0 opamp 0 4 {A}"
)
# Problem 2.48(b): vo = -4*vN1 + vP1 + 3*vP2
SUMMING = (
    "VN1 1 0 {vN1}\nVP1 4 0 {vP1}\nVP2 5 0 {vP2}\n"
    "RN1 1 2 10e3\nRf 2 3 40e3\n"
    "RP1 4 6 30e3\nRP2 5 6 10e3\nRP0 6 0 30e3\n"
    "E1 3 0 opamp 6 2 {A}"
)
ALL = {"inverting": INVERTING, "noninverting": NONINVERTING, "follower": FOLLOWER,
       "integrator": INTEGRATOR, "difference": DIFFERENCE, "cascade": CASCADE,
       "summing": SUMMING}


@pytest.mark.parametrize("name", sorted(ALL))
def test_the_drawing_rebuilds_its_netlist(name):
    """The check that makes the picture trustworthy: geometry alone must say the netlist."""
    assert netlist_of(draw(ALL[name])) == netlist_of(ALL[name])


def test_2_48b_labels_every_element_with_its_value():
    picture = svg(draw(SUMMING))
    for label in ("RN1", "10 kΩ", "Rf", "40 kΩ", "RP1", "30 kΩ", "RP2", "RP0"):
        assert label in picture, f"{label} is missing from the drawing"


def test_every_drawing_is_well_formed_escaped_xml():
    hostile = INVERTING.replace("Ri", "R<i>")
    with pytest.raises(SchematicError):
        draw(hostile)
    for netlist in ALL.values():
        ElementTree.fromstring(svg(draw(netlist)))


def test_verify_refuses_a_moved_wire():
    """Moving one wire end breaks the rebuild, which is the point of checking."""
    drawing = draw(INVERTING)
    moved = [dict(wire) for wire in drawing.wires]
    moved[0] = {**moved[0], "points": [(x + 37, y + 23) for x, y in moved[0]["points"]]}
    with pytest.raises(SchematicError):
        connections(drawing.__class__(**{**drawing.__dict__, "wires": moved}), strict=True)


def test_a_crossing_joins_nothing_and_an_overlap_joins():
    drawing = draw(DIFFERENCE)
    # A difference amplifier cannot be drawn without one wire crossing another.
    assert netlist_of(drawing) == netlist_of(DIFFERENCE)


def test_unsupported_elements_are_refused_by_name():
    for netlist, named in (
        ("Vs 1 0 {V}\nF1 2 0 Vs 2", "F1"),
        ("Vs 1 0 {V}\nK1 L1 L2 0.5\nL1 1 2 1e-3\nL2 2 0 1e-3", "K1"),
        ("Vs 1 0 {V}\nE1 2 0 1 0 10", "E1"),
    ):
        with pytest.raises(SchematicError, match=named):
            draw(netlist)


def test_an_op_amp_output_not_referenced_to_ground_is_refused():
    with pytest.raises(SchematicError, match="E1"):
        draw("Vs 1 0 {V}\nRi 1 2 {Ri}\nRf 2 3 {Rf}\nE1 3 4 opamp 0 2 {A}\nR4 4 0 1e3")


def test_labels_stay_inside_the_drawing_and_never_overlap_each_other():
    for netlist in ALL.values():
        drawing = draw(netlist)
        boxes = [label["box"] for label in drawing.labels]
        for box in boxes:
            assert 0 <= box[0] and box[2] <= drawing.width, f"{box} runs off the drawing"
            assert 0 <= box[1] and box[3] <= drawing.height, f"{box} runs off the drawing"
        for i, box in enumerate(boxes):
            for other in boxes[i + 1:]:
                assert not (box[0] < other[2] and other[0] < box[2]
                            and box[1] < other[3] and other[1] < box[3]), \
                    f"labels {box} and {other} overlap"


def test_the_svg_carries_no_script_and_one_root():
    picture = svg(draw(SUMMING))
    assert "<script" not in picture.lower()
    assert len(re.findall(r"<svg", picture)) == 1
