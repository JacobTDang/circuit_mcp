"""What the bench should read. Numbers are checked against the hand analysis of Lab 1.

ngspice runs for real here, as it does in test_spice.py: the point of these tests is
that the model agrees with the textbook on the circuits students actually build.
"""
from __future__ import annotations

import copy
import math

import pytest

from circuit_mcp.breadboard import parse_build
from circuit_mcp.expect import ExpectError, deck, expectations
from tests.fixtures import lab1


def reading(result, label):
    return next(r for r in result["readings"] if r["label"] == label)


def test_the_deck_for_exp1_is_complete_and_deterministic():
    text = deck(parse_build(lab1.EXP1_NONINVERTING))
    assert ".subckt railamp" in text
    assert "Vplus vplus 0 dc 15" in text and "Vminus vminus 0 dc -15" in text
    assert "RR3a vsine vi 5000" in text and "RR3b vi 0 5000" in text
    assert "RR1 fb 0 1000" in text and "RR2 out fb 15000" in text
    assert "XU1A vi fb out vplus vminus railamp hi=1.5 lo=0.5" in text
    assert "VVS vsine 0 dc 0 SIN(0 1.41421 1000)" in text
    assert text == deck(parse_build(lab1.EXP1_NONINVERTING))


def test_single_supply_ties_the_chip_to_ground_and_models_the_led():
    text = deck(parse_build(lab1.EXP4B_BUFFERED))
    assert "XU1A va vb vb vplus 0 railamp" in text
    assert "DD1 vd 0 led" in text
    assert ".model led D(IS=" in text


def test_a_square_wave_becomes_a_pulse_source():
    text = deck(parse_build(lab1.EXP5_INTEGRATOR))
    assert "VVS vi 0 dc 0 PULSE(-5 5 0 2e-06 2e-06 0.000998 0.002)" in text


def test_exp1_gain_is_sixteen_in_phase_and_unclipped():
    result = expectations(lab1.EXP1_NONINVERTING)
    gain = result["gains"][0]
    assert math.isclose(gain["gain"], 16.0, rel_tol=0.02) and gain["phase"] == "in phase"
    assert reading(result, "CH2 vo")["clipped"] is False
    assert math.isclose(reading(result, "CH1 vi")["vrms"], 0.5, rel_tol=0.02)


def test_exp1_clips_when_the_pot_is_turned_up():
    hot = copy.deepcopy(lab1.EXP1_NONINVERTING)
    hot["pot_positions"] = {"R3": 0.95}
    result = expectations(hot)
    assert reading(result, "CH2 vo")["clipped"] is True
    assert result["swing"] == {"low": -14.5, "high": 13.5}
    assert any("clipping" in note for note in result["notes"])


def test_exp3_inverting_gain_and_phase():
    gain = expectations(lab1.EXP3_INVERTING)["gains"][0]
    assert math.isclose(gain["gain"], 50 / 2.2, rel_tol=0.02) and gain["phase"] == "inverted"


def test_exp4_divider_with_and_without_the_buffer():
    loaded = expectations(lab1.EXP4A_DIVIDER_LED)
    assert math.isclose(reading(loaded, "VA")["volts"], 5.51, abs_tol=0.05)
    buffered = expectations(lab1.EXP4B_BUFFERED)
    assert math.isclose(reading(buffered, "VA")["volts"], 11.33, abs_tol=0.02)
    assert math.isclose(reading(buffered, "VB")["volts"], reading(buffered, "VA")["volts"], abs_tol=0.01)


def test_exp5_integrator_is_a_five_volt_triangle_in_quadrature():
    result = expectations(lab1.EXP5_INTEGRATOR)
    out = reading(result, "CH2 vo")
    assert math.isclose(out["vpp"], 5.0, rel_tol=0.05) and abs(out["mean"]) < 0.1
    assert out["clipped"] is False
    assert result["gains"][0]["phase"].startswith("quadrature")
    assert result["analysis"].startswith("tran 1e-05 0.239 0.235")   # settle = 5 * 470k * 0.1u


def test_exp6_exp7_exp8_match_the_hand_analysis():
    assert math.isclose(expectations(lab1.EXP6_DIFFERENCE)["gains"][0]["gain"], 10.0, rel_tol=0.02)
    assert math.isclose(reading(expectations(lab1.EXP7_DAC), "vo")["volts"], -5.0, rel_tol=0.01)
    in_amp = expectations(lab1.EXP8_INSTRUMENTATION)["gains"][0]
    assert math.isclose(in_amp["gain"], 30.0, rel_tol=0.02) and in_amp["phase"] == "in phase"


def test_readings_carry_a_sparkline_only_for_ac():
    ac = reading(expectations(lab1.EXP1_NONINVERTING), "CH2 vo")
    assert ac["sparkline"].startswith("<svg") and "<polyline" in ac["sparkline"]
    dc = reading(expectations(lab1.EXP7_DAC), "vo")
    assert "sparkline" not in dc


def test_a_build_without_probes_or_sources_is_refused():
    silent = copy.deepcopy(lab1.EXP1_NONINVERTING)
    silent["probes"] = []
    with pytest.raises(ExpectError, match="at least one probe"):
        expectations(silent)
    dark = copy.deepcopy(lab1.EXP1_NONINVERTING)
    dark["sources"] = []
    with pytest.raises(ExpectError, match="at least one source"):
        expectations(dark)
