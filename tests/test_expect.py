"""What the bench should read. Numbers are checked against the hand analysis of Lab 1.

ngspice runs for real here, as it does in test_spice.py: the point of these tests is
that the model agrees with the textbook on the circuits students actually build.
"""
from __future__ import annotations

import copy
import math

import pytest

from circuit_mcp import spice
from circuit_mcp.breadboard import parse_build
from circuit_mcp.expect import ExpectError, deck, expectations
from tests.fixtures import lab1


def reading(result, label):
    return next(r for r in result["readings"] if r["label"] == label)


def part(build, ref):
    """A part by its ref. Indexing by position breaks the moment a fixture gains a part."""
    return next(entry for entry in build["parts"] if entry["ref"] == ref)


def probe(build, label):
    return next(entry for entry in build["probes"] if entry["label"] == label)


def test_the_deck_for_exp1_is_complete_and_deterministic():
    text = deck(parse_build(lab1.EXP1_NONINVERTING))
    assert ".subckt railamp" in text
    assert "V_plus vplus 0 dc 15" in text and "V_minus vminus 0 dc -15" in text
    assert "RR3_a vsine vi 5000" in text and "RR3_b vi 0 5000" in text
    assert "RR1 fb 0 1000" in text and "RR2 out fb 15000" in text
    assert "XU1A vi fb out vplus vminus railamp hi=1.5 lo=0.5" in text
    assert "VVS vsine 0 dc 0 SIN(0 1.41421 1000)" in text
    assert text == deck(parse_build(lab1.EXP1_NONINVERTING))


def test_no_ref_makes_a_device_name_ngspice_already_has():
    """A pot's halves and the rail sources share ngspice's one case-blind namespace with
    the build's refs: pot R3 became RR3a beside resistor R3a, and source plus became
    Vplus beside the rail, and ngspice refused the second of each."""
    build = {
        "supply": {"vplus": 5, "vminus": -5},
        "parts": [{"ref": "R3", "kind": "pot", "value": "10k", "nodes": ["top", "mid", "gnd"]},
                  {"ref": "R3a", "kind": "resistor", "value": "1k", "nodes": ["a", "mid"]},
                  {"ref": "R3b", "kind": "resistor", "value": "1k", "nodes": ["mid", "gnd"]}],
        "sources": [{"ref": "plus", "kind": "dc", "node": "top", "volts": 1},
                    {"ref": "Minus", "kind": "dc", "node": "a", "volts": 2}],
        "probes": [{"label": "mid", "node": "mid"}],
    }
    text = deck(parse_build(build))
    body = text.split(".ends railamp", 1)[1]
    names = [line.split()[0] for line in body.splitlines() if line and not line.startswith(".")]
    assert len({name.casefold() for name in names}) == len(names), names
    # 5k up to 1 V, 1k up to 2 V, and 5k parallel 1k down: 11/12 V by nodal analysis.
    assert math.isclose(reading(expectations(build), "mid")["volts"], 11 / 12, abs_tol=1e-3)


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
    assert ("CH2 vo is clipping: the op amp output cannot go beyond about -14.5 V to 13.5 V on this supply."
            in result["notes"])


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


def test_an_upper_case_node_name_is_read_back_from_ngspice():
    """ngspice lower-cases its vector names; a build may capitalise a net."""
    shouty = copy.deepcopy(lab1.EXP1_NONINVERTING)
    part(shouty, "R2")["nodes"] = ["Out", "fb"]
    shouty["opamps"][0]["out"] = "Out"
    probe(shouty, "CH2 vo")["node"] = "Out"
    result = expectations(shouty)
    assert math.isclose(result["gains"][0]["gain"], 16.0, rel_tol=0.02)
    assert reading(result, "CH2 vo")["node"] == "Out"


def test_a_settle_window_the_simulation_cannot_resolve_is_refused():
    """A huge RC used to stretch the step until the capture window held two points."""
    slow = copy.deepcopy(lab1.EXP5_INTEGRATOR)
    part(slow, "R2")["value"] = "10meg"   # tau = 1 s, so a 5 s settle at 500 Hz
    with pytest.raises(ExpectError, match="settle window"):
        expectations(slow)


def test_a_failed_simulation_is_reported_with_its_cause(monkeypatch):
    def boom(netlist, analysis, outputs):
        raise spice.SpiceError("boom")

    monkeypatch.setattr("circuit_mcp.expect.spice.simulate_spice", boom)
    with pytest.raises(ExpectError, match="simulation failed: boom") as raised:
        expectations(lab1.EXP1_NONINVERTING)
    assert isinstance(raised.value.__cause__, spice.SpiceError)


def test_a_simulation_that_returns_nothing_is_refused(monkeypatch):
    monkeypatch.setattr("circuit_mcp.expect.spice.simulate_spice",
                        lambda netlist, analysis, outputs: {"ok": True, "analysis": "op", "points": []})
    with pytest.raises(ExpectError, match="no points"):
        expectations(lab1.EXP1_NONINVERTING)


def test_the_result_carries_the_deck_that_was_simulated():
    assert expectations(lab1.EXP1_NONINVERTING)["deck"] == deck(parse_build(lab1.EXP1_NONINVERTING))


def test_a_flat_reading_is_not_called_quadrature():
    """A trace that never moves has no phase. The correlation denominator used to
    fall back to 1, which made zero over zero look like a quarter period apart."""
    held = copy.deepcopy(lab1.EXP6_DIFFERENCE)
    probe(held, "CH2 vo").update(label="CH2 vb", node="vb", role="output")
    gain = expectations(held)["gains"][0]
    assert gain["phase"] == "flat"
    assert gain["gain"] == 0.0


def test_the_expectation_names_the_pot_positions_its_numbers_depend_on():
    assert expectations(lab1.EXP1_NONINVERTING)["pots"] == {"R3": 0.5}
    assert expectations(lab1.EXP7_DAC)["pots"] == {}


def test_a_pot_at_either_end_says_it_is_modelled_as_one_ohm():
    """The clamp used to be silent, so a reading taken at an end looked exact."""
    build = copy.deepcopy(lab1.EXP1_NONINVERTING)
    build["pot_positions"]["R3"] = 0.0
    notes = expectations(build)["notes"]
    assert any("R3" in note and "1" in note for note in notes), notes

    build["pot_positions"]["R3"] = 1.0
    assert any("R3" in note for note in expectations(build)["notes"])


def test_a_pot_off_its_ends_says_nothing_about_the_clamp():
    assert not [note for note in expectations(lab1.EXP1_NONINVERTING)["notes"] if "1 " in note and "R3" in note]


def test_a_dc_build_with_no_input_probe_reports_no_gains():
    """The front end renders an empty gains list; this pins that shape."""
    result = expectations(lab1.EXP7_DAC)
    assert result["gains"] == []


TWO_FOLLOWERS = {
    "supply": {"vplus": 12, "vminus": -12},
    "chips": [{"ref": "U1", "part": "LM324"}, {"ref": "U2", "part": "LM324"}],
    "parts": [{"ref": "R1", "kind": "resistor", "value": "10k", "nodes": ["o1", "n1"]},
              {"ref": "R2", "kind": "resistor", "value": "10k", "nodes": ["o2", "n2"]}],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "vin", "inn": "n1", "out": "o1"},
               {"ref": "U2A", "chip": "U2", "section": "A", "inp": "o1", "inn": "n2", "out": "o2"}],
    "sources": [{"ref": "VS", "kind": "sine", "node": "vin", "vrms": 0.1, "freq": 1000}],
    "probes": [{"label": "in", "node": "vin", "role": "input"}, {"label": "out", "node": "o2", "role": "output"}],
}


def test_two_chips_of_one_part_are_predicted_with_that_parts_swing():
    result = expectations(TWO_FOLLOWERS)
    assert result["gains"][0]["gain"] == pytest.approx(1.0, abs=0.01)
    assert result["swing"] == {"low": -11.5, "high": 10.5}


# The issue #126 cascade, an LM324 follower into an LMC660 one, at a supply the LMC660 takes, probed after each stage.
ISSUE_126_CASCADE = {**TWO_FOLLOWERS, "supply": {"vplus": 7.5, "vminus": -7.5},
                     "chips": [{"ref": "U1", "part": "LM324"}, {"ref": "U2", "part": "LMC660"}],
                     "probes": TWO_FOLLOWERS["probes"] + [{"label": "mid", "node": "o1", "role": "output"}]}


def test_a_build_mixing_an_lm324_and_an_lmc660_names_each_parts_swing():
    result = expectations(ISSUE_126_CASCADE)
    assert result["gains"][0]["gain"] == pytest.approx(1.0, abs=0.01)
    assert result["swing"] == [{"part": "LM324", "low": -7.0, "high": 6.0},
                               {"part": "LMC660", "low": -7.4, "high": 7.4}]


def test_each_output_of_a_mixed_build_is_judged_by_its_own_chips_swing():
    """6.5 V peak in: the LM324 stage clips at its 6 V limit; the LMC660 stage after it is 1.4 V clear of its own."""
    hot = copy.deepcopy(ISSUE_126_CASCADE)
    hot["sources"][0]["vrms"] = 6.5 / math.sqrt(2)
    result = expectations(hot)
    assert reading(result, "mid")["clipped"] is True
    assert reading(result, "out")["clipped"] is False
    assert "mid is clipping: the LM324 output cannot go beyond about -7 V to 6 V on this supply." in result["notes"]
    assert not [note for note in result["notes"] if note.startswith("out ")]


AC_FIELDS = {"label", "node", "role", "kind", "vpp", "vpk", "vrms", "mean", "clipped", "sparkline"}
DC_FIELDS = {"label", "node", "role", "kind", "volts"}
LMC660_PAIR = {**TWO_FOLLOWERS, "supply": {"vplus": 7.5, "vminus": -7.5},
               "chips": [{"ref": "U1", "part": "LMC660"}, {"ref": "U2", "part": "LMC660"}]}


@pytest.mark.parametrize("build", [lab1.EXP1_NONINVERTING, lab1.EXP4B_BUFFERED, lab1.EXP5_INTEGRATOR,
                                   lab1.EXP7_DAC, lab1.EXP8_INSTRUMENTATION, TWO_FOLLOWERS, LMC660_PAIR],
                         ids=["exp1", "exp4b", "exp5", "exp7", "exp8", "two-lm324", "two-lmc660"])
def test_a_build_of_one_part_keeps_its_one_unnamed_swing(build):
    """Only a build mixing parts reports a swing per part; every other payload is the one it always was."""
    parsed = parse_build(build)
    chip = next(iter(parsed.chips.values()))
    result = expectations(build)
    assert set(result) == {"analysis", "readings", "gains", "swing", "notes", "pots", "deck"}
    assert result["swing"] == {"low": parsed.vminus + chip.headroom_low, "high": parsed.vplus - chip.headroom_high}
    for r in result["readings"]:
        assert set(r) == (AC_FIELDS if r["kind"] == "ac" else DC_FIELDS), r
