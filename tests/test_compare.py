"""Measured readings against the build's prediction, on the Lab 1 circuits.

ngspice runs for real, as in test_expect.py: the prediction side is the same
expectations() the expected card uses.
"""
from __future__ import annotations

import copy

import pytest

from circuit_mcp.compare import CompareError, compare_readings
from circuit_mcp.expect import expectations
from tests.fixtures import lab1


def entry(result, label):
    return next(r for r in result["readings"] if r["label"] == label)


def predicted(build):
    return {r["label"]: r for r in expectations(build)["readings"]}


def test_readings_that_match_the_prediction_pass():
    p = predicted(lab1.EXP1_NONINVERTING)
    result = compare_readings(lab1.EXP1_NONINVERTING,
                              {"CH1 vi": p["CH1 vi"]["vrms"], "CH2 vo": p["CH2 vo"]["vrms"]})
    assert result["ok"] is True
    assert result["tolerance_pct"] == 5.0
    assert result["all_within_tolerance"] is True
    assert result["gains"][0]["within_tolerance"] is True
    assert "hint" not in entry(result, "CH1 vi")


def test_exp1_probe_on_the_generator_is_flagged_as_a_source_reading():
    # The Lab 1 write-up: CH1 read the generator's 1 V RMS, not vi at the op-amp pin.
    result = compare_readings(lab1.EXP1_NONINVERTING, {"CH1 vi": 0.9955, "CH2 vo": 7.99})
    vi = entry(result, "CH1 vi")
    assert vi["within_tolerance"] is False
    assert vi["hint"]["kind"] == "source"
    assert "VS" in vi["hint"]["message"]
    gain = result["gains"][0]
    assert gain["measured"] == pytest.approx(8.03, abs=0.01)
    assert gain["within_tolerance"] is False
    assert result["all_within_tolerance"] is False


def test_dac_output_with_a_dropped_minus_sign_is_a_sign_error():
    vo = entry(compare_readings(lab1.EXP7_DAC, {"vo": 5.0}), "vo")
    assert vo["within_tolerance"] is False
    assert vo["hint"]["kind"] == "sign"


def test_dac_output_at_the_negative_rail_is_clipping():
    vo = entry(compare_readings(lab1.EXP7_DAC, {"vo": -9.45}), "vo")
    assert vo["hint"]["kind"] == "rail"


def test_a_zero_volt_expectation_uses_an_absolute_band():
    build = copy.deepcopy(lab1.EXP7_DAC)
    for source in build["sources"]:
        source["volts"] = 0          # code 000
    near = entry(compare_readings(build, {"vo": 0.02}), "vo")
    assert near["error_pct"] is None
    assert near["within_tolerance"] is True
    assert entry(compare_readings(build, {"vo": 0.2}), "vo")["within_tolerance"] is False


def test_peak_to_peak_readings_are_compared_in_their_own_basis():
    p = predicted(lab1.EXP1_NONINVERTING)
    vo = entry(compare_readings(lab1.EXP1_NONINVERTING, {"CH2 vo": {"vpp": p["CH2 vo"]["vpp"]}}), "CH2 vo")
    assert vo["basis"] == "vpp"
    assert vo["within_tolerance"] is True


def test_an_unknown_probe_label_is_an_error_not_dropped():
    with pytest.raises(CompareError, match="no probe labelled 'CH3'"):
        compare_readings(lab1.EXP1_NONINVERTING, {"CH3": 1.0})


@pytest.mark.parametrize("measured", [
    {},
    {"vo": "5"},
    {"vo": float("nan")},
    {"vo": True},
    {"vo": {"vrms": 1.0}},              # vo is a DC probe
    {"vo": {"volts": 1, "vrms": 1}},    # two bases for one reading
])
def test_malformed_measurements_are_refused(measured):
    with pytest.raises(CompareError):
        compare_readings(lab1.EXP7_DAC, measured)


def test_a_non_positive_tolerance_is_refused():
    with pytest.raises(CompareError, match="tolerance_pct"):
        compare_readings(lab1.EXP7_DAC, {"vo": -5.0}, tolerance_pct=0)


def test_a_build_that_cannot_be_simulated_is_a_compare_error():
    with pytest.raises(CompareError, match="cannot predict"):
        compare_readings({"supply": {}}, {"vo": 1.0})


def fed_back(build):
    """Every probe's own predicted V RMS, as a scope's RMS measurement would read it."""
    return {label: reading["vrms"] for label, reading in predicted(build).items()}


def test_gains_use_the_predicted_waveform_shape_not_a_sine():
    # Square in, triangle out: neither has a sine's sqrt(2) peak-to-RMS ratio.
    result = compare_readings(lab1.EXP5_INTEGRATOR, fed_back(lab1.EXP5_INTEGRATOR))
    assert result["all_within_tolerance"] is True
    assert result["gains"][0]["within_tolerance"] is True


def test_a_clipped_output_read_as_predicted_keeps_its_gain():
    build = copy.deepcopy(lab1.EXP1_NONINVERTING)
    build["sources"][0]["vrms"] = 3.0     # vi = 1.5 V RMS; 16 times that is far past the 15 V rails
    assert predicted(build)["CH2 vo"]["clipped"] is True
    result = compare_readings(build, fed_back(build))
    assert result["gains"][0]["within_tolerance"] is True


@pytest.mark.parametrize("reading", [
    {"vpk": 13.45},
    9.6,                                   # V RMS: a sine with its 13.5 V peak on the rail
])
def test_an_ac_output_on_the_rail_is_clipping(reading):
    vo = entry(compare_readings(lab1.EXP1_NONINVERTING, {"CH2 vo": reading}), "CH2 vo")
    assert vo["hint"]["kind"] == "rail"


def test_a_dc_probe_reading_its_source_setting_is_flagged_as_a_source_reading():
    va = entry(compare_readings(lab1.EXP4A_DIVIDER_LED, {"VA": 15.0}), "VA")
    assert va["hint"]["kind"] == "source"
    assert "VDC" in va["hint"]["message"]


def test_the_tolerance_sets_the_verdict():
    assert entry(compare_readings(lab1.EXP7_DAC, {"vo": -5.3}), "vo")["within_tolerance"] is False
    assert entry(compare_readings(lab1.EXP7_DAC, {"vo": -5.3}, tolerance_pct=10), "vo")["within_tolerance"] is True


def test_repeated_probe_labels_are_refused():
    build = copy.deepcopy(lab1.EXP1_NONINVERTING)
    for probe in build["probes"]:
        probe["label"] = "CH1"
    with pytest.raises(CompareError, match="'CH1'"):
        compare_readings(build, {"CH1": 0.5})


def test_a_gain_with_no_measurable_input_is_reported_not_dropped():
    gain = compare_readings(lab1.EXP1_NONINVERTING, {"CH1 vi": 0.0005, "CH2 vo": 7.99})["gains"][0]
    assert gain["measured"] is None
    assert gain["error_pct"] is None
    assert gain["within_tolerance"] is False
    assert "1 mV" in gain["reason"]
