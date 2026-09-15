"""The part library: pin numbers a student wires from, checked against the datasheet."""
from __future__ import annotations

import math

import pytest

from circuit_mcp.parts import (
    CHIPS, QUAD_DIP14, PartError, chip, led_saturation_current, opamp_subcircuit, parse_value,
)


def test_quad_dip14_pin_map_matches_the_datasheet():
    # LM324 and LMC660 datasheets: OUT1=1, IN1-=2, IN1+=3, V+=4, IN2+=5, IN2-=6, OUT2=7,
    # OUT3=8, IN3-=9, IN3+=10, V-=11, IN4+=12, IN4-=13, OUT4=14.
    expected = {"A": (1, 2, 3), "B": (7, 6, 5), "C": (8, 9, 10), "D": (14, 13, 12)}
    assert {s.name: (s.out, s.inn, s.inp) for s in QUAD_DIP14} == expected
    for name in ("LM324", "LMC660"):
        assert (CHIPS[name].vplus, CHIPS[name].vminus, CHIPS[name].pins) == (4, 11, 14)


def test_every_pin_of_the_quad_package_is_used_exactly_once():
    pins = [4, 11] + [p for s in QUAD_DIP14 for p in (s.out, s.inn, s.inp)]
    assert sorted(pins) == list(range(1, 15))


def test_chip_lookup_is_case_insensitive_and_loud():
    assert chip("lm324").part == "LM324"
    with pytest.raises(PartError, match="unknown chip"):
        chip("NE555")


def test_section_lookup_is_case_insensitive_and_loud():
    assert chip("LMC660").section("b").out == 7
    with pytest.raises(PartError, match="no section"):
        chip("LMC660").section("E")


def test_the_lm324_cannot_reach_its_positive_rail_but_the_lmc660_nearly_can():
    assert chip("LM324").headroom_high == 1.5
    assert chip("LMC660").headroom_high <= 0.1


@pytest.mark.parametrize("text,value", [
    ("1k", 1e3), ("15k", 15e3), ("2.2k", 2200.0), ("0.1u", 1e-7), ("10n", 1e-8),
    ("470k", 470e3), ("250m", 0.25), ("1meg", 1e6), ("100", 100.0), ("4.7 kΩ", 4700.0),
])
def test_component_values_parse(text, value):
    assert math.isclose(parse_value(text), value, rel_tol=1e-9)


@pytest.mark.parametrize("bad", ["", "k", "1x", "ten", "1,000", None])
def test_unreadable_values_are_refused(bad):
    with pytest.raises(PartError, match="cannot read"):
        parse_value(bad)


def test_led_model_puts_two_volts_across_the_diode_at_three_milliamps():
    saturation = led_saturation_current(2.0, 3e-3, 2.0)
    current = saturation * math.exp(2.0 / (2.0 * 0.02585))
    assert math.isclose(current, 3e-3, rel_tol=1e-9)


def test_opamp_subcircuit_clamps_with_named_headroom():
    text = opamp_subcircuit()
    assert ".subckt railamp inp inn out vp vn params: hi=1.5 lo=0.5" in text
    assert "{hi}" in text and "{lo}" in text
    assert text.strip().endswith(".ends railamp")
