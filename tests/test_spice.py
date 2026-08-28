"""Independent numeric acceptance cases for the local ngspice backend."""
from __future__ import annotations

import math

import pytest

from circuit_mcp.spice import SpiceError, simulate_spice


def test_operating_point_voltage_divider_and_source_current():
    result = simulate_spice(
        "V1 in 0 10\nR1 in out 1k\nR2 out 0 2k", "op", ["v(out)", "i(v1)"]
    )
    point = result["points"][0]
    assert point["v(out)"] == pytest.approx(20 / 3, rel=1e-12)
    assert point["i(v1)"] == pytest.approx(-1 / 300, rel=1e-12)


def test_dc_sweep_routes_parameters_and_returns_every_requested_point():
    result = simulate_spice(
        "V1 in 0 0\nR1 in out 1k\nR2 out 0 1k",
        "dc V1 0 5 1",
        ["v(v-sweep)", "v(out)"],
    )
    assert len(result["points"]) == 6
    for voltage, point in enumerate(result["points"]):
        assert point["v(v-sweep)"] == pytest.approx(voltage)
        assert point["v(out)"] == pytest.approx(voltage / 2)


def test_ac_rc_lowpass_matches_closed_form_at_cutoff():
    result = simulate_spice(
        "V1 in 0 AC 1\nR1 in out 1k\nC1 out 0 1u",
        "ac lin 1 159.154943 159.154943",
        ["v(out)"],
    )
    value = result["points"][0]["v(out)"]
    assert value["real"] == pytest.approx(0.5, rel=2e-6)
    assert value["imag"] == pytest.approx(-0.5, rel=2e-6)
    assert math.hypot(value["real"], value["imag"]) == pytest.approx(1 / math.sqrt(2), rel=2e-6)
    assert result["variables"][0]["name"] == "frequency"


def test_transient_rc_step_matches_one_minus_exponential():
    result = simulate_spice(
        "V1 in 0 PULSE(0 1 0 1n 1n 10m 20m)\nR1 in out 1k\nC1 out 0 1u",
        "tran 10u 1m",
        ["time", "v(out)"],
    )
    final = result["points"][-1]
    assert final["time"] == pytest.approx(1e-3)
    assert final["v(out)"] == pytest.approx(1 - math.exp(-1), abs=2e-4)


def test_nonlinear_diode_operating_point_is_physically_consistent():
    result = simulate_spice(
        "V1 in 0 5\nR1 in diode 1k\nD1 diode 0 DIO\n.model DIO D(Is=1e-14 N=1)",
        "op",
        ["v(diode)", "i(v1)"],
    )
    point = result["points"][0]
    assert 0.5 < point["v(diode)"] < 0.8
    assert point["i(v1)"] == pytest.approx(-(5 - point["v(diode)"]) / 1000, rel=1e-7)


def test_ac_analysis_linearizes_a_nonlinear_diode_at_its_bias_point():
    deck = "V1 in 0 DC 5 AC 1\nR1 in diode 1k\nD1 diode 0 DIO\n.model DIO D(Is=1e-14 N=1)"
    operating = simulate_spice(deck, "op", ["i(v1)"])["points"][0]
    ac = simulate_spice(deck, "ac lin 1 1 1", ["v(diode)"])["points"][0]
    current = -operating["i(v1)"]
    dynamic_resistance = 0.02586 / current
    expected_gain = dynamic_resistance / (1000 + dynamic_resistance)
    assert ac["v(diode)"]["real"] == pytest.approx(expected_gain, rel=0.015)
    assert ac["v(diode)"]["imag"] == pytest.approx(0, abs=1e-10)


def test_bjt_dc_bias_obeys_collector_kvl_and_forward_active_beta():
    result = simulate_spice(
        "VCC vcc 0 5\nVB base 0 0.7\nRC vcc collector 1k\nQ1 collector base 0 NPN\n.model NPN NPN(Is=1e-15 Bf=100)",
        "op", ["v(collector)", "i(vcc)", "i(vb)"],
    )["points"][0]
    collector_current = -result["i(vcc)"]
    base_current = -result["i(vb)"]
    assert result["v(collector)"] == pytest.approx(5 - 1000 * collector_current, rel=1e-8)
    assert collector_current / base_current == pytest.approx(100, rel=0.01)
    assert result["v(collector)"] > 0.7  # forward-active, not saturated


def test_level_one_mos_bias_matches_square_law_saturation_model():
    result = simulate_spice(
        "VDD vdd 0 5\nVG gate 0 2\nRD vdd drain 1k\nM1 drain gate 0 0 NM\n.model NM NMOS(Level=1 Vto=1 Kp=1m Lambda=0)",
        "op", ["v(drain)", "i(vdd)"],
    )["points"][0]
    assert -result["i(vdd)"] == pytest.approx(0.5e-3, rel=1e-4)
    assert result["v(drain)"] == pytest.approx(4.5, rel=1e-4)


def test_bjt_common_emitter_small_signal_gain_matches_minus_gm_rc():
    deck = (
        "VCC vcc 0 5\nVB base 0 DC 0.7 AC 1\nRC vcc collector 1k\n"
        "Q1 collector base 0 NPN\n.model NPN NPN(Is=1e-15 Bf=100 Vaf=1e9)"
    )
    operating = simulate_spice(deck, "op", ["i(vcc)"])["points"][0]
    response = simulate_spice(deck, "ac lin 1 1 1", ["v(collector)"])["points"][0]
    gm = (-operating["i(vcc)"]) / 0.02586
    assert response["v(collector)"]["real"] == pytest.approx(-gm * 1000, rel=0.02)


def test_mos_common_source_small_signal_gain_matches_minus_gm_rd():
    deck = (
        "VDD vdd 0 5\nVG gate 0 DC 2 AC 1\nRD vdd drain 1k\n"
        "M1 drain gate 0 0 NM\n.model NM NMOS(Level=1 Vto=1 Kp=1m Lambda=0)"
    )
    response = simulate_spice(deck, "ac lin 1 1 1", ["v(drain)"])["points"][0]
    assert response["v(drain)"]["real"] == pytest.approx(-1, rel=1e-4)
    assert response["v(drain)"]["imag"] == pytest.approx(0, abs=1e-12)


def test_diode_half_wave_rectifier_passes_positive_and_blocks_negative_input():
    result = simulate_spice(
        "V1 in 0 0\nD1 in out DIO\nR1 out 0 1k\n.model DIO D(Is=1e-14)",
        "dc V1 -2 2 2", ["v(v-sweep)", "v(out)"],
    )["points"]
    assert result[0]["v(out)"] == pytest.approx(0, abs=3e-9)
    assert result[1]["v(out)"] == pytest.approx(0, abs=2e-11)
    assert 1.2 < result[2]["v(out)"] < 1.5


def test_bounded_behavioral_comparator_saturates_at_both_rails():
    result = simulate_spice(
        "VIN in 0 0\nB1 out 0 V=5*TANH(1e6*V(in))\nR1 out 0 1k",
        "dc VIN -1m 1m 1m", ["v(out)"],
    )["points"]
    assert [point["v(out)"] for point in result] == pytest.approx([-5, 0, 5])


def test_transfer_analysis_returns_gain_and_port_impedances():
    result = simulate_spice(
        "V1 in 0 1\nR1 in out 1k\nR2 out 0 1k", "tf v(out) V1", []
    )["points"][0]
    assert result["v(transfer_function)"] == pytest.approx(0.5)
    assert result["v(v1#input_impedance)"] == pytest.approx(2000)
    assert result["v(output_impedance_at_v(out))"] == pytest.approx(500)


def test_pole_zero_analysis_finds_rc_pole():
    result = simulate_spice(
        "V1 in 0 AC 1\nR1 in out 1k\nC1 out 0 1u",
        "pz in 0 out 0 vol pz", [],
    )["points"][0]
    assert result["v(pole(1))"]["real"] == pytest.approx(-1000, rel=1e-10)
    assert result["v(pole(1))"]["imag"] == pytest.approx(0)


def test_noise_analysis_matches_resistor_johnson_noise_at_low_frequency():
    result = simulate_spice(
        "V1 in 0 DC 0 AC 1\nR1 in out 1k\nC1 out 0 1u",
        "noise v(out) V1 lin 1 1 1", ["onoise_spectrum"],
    )["points"][0]
    # sqrt(4*k*T*R) at ngspice's default 300.15 K, with the capacitor open.
    expected = math.sqrt(4 * 1.380649e-23 * 300.15 * 1000)
    assert result["onoise_spectrum"] == pytest.approx(expected, rel=0.01)


def test_distortion_analysis_produces_a_second_harmonic_for_a_biased_diode():
    result = simulate_spice(
        "V1 in 0 DC 1 AC 0.01 DISTOF1 0.01\nR1 in out 1k\nD1 out 0 DIO\n.model DIO D(Is=1e-14)",
        "disto lin 1 1k 1k", ["v(out)"],
    )["points"][0]["v(out)"]
    assert math.hypot(result["real"], result["imag"]) > 0


def test_dc_sensitivity_matches_analytic_divider_derivatives():
    point = simulate_spice(
        "V1 in 0 1\nR1 in out 1k\nR2 out 0 1k",
        "sens v(out)", ["v(r1)", "v(r2)"],
    )["points"][0]
    assert point["v(r1)"] == pytest.approx(-0.00025, rel=2e-6)
    assert point["v(r2)"] == pytest.approx(0.00025, rel=2e-6)


def test_xspice_adc_to_dac_bridge_round_trips_a_digital_level():
    deck = (
        "Vin ain 0 PULSE(0 1 0 1n 1n 1u 2u)\n"
        "A1 [ain] [d] ADCMOD\nA2 [d] [aout] DACMOD\n"
        ".model ADCMOD adc_bridge(in_low=0.4 in_high=0.6 rise_delay=1n fall_delay=1n)\n"
        ".model DACMOD dac_bridge(out_low=0 out_high=1 out_undef=0.5 input_load=1p t_rise=1n t_fall=1n)\n"
        "R1 aout 0 1k"
    )
    points = simulate_spice(deck, "tran 50n 2u", ["v(aout)"])["points"]
    values = [point["v(aout)"] for point in points]
    assert min(values) == pytest.approx(0)
    assert max(values) == pytest.approx(1)


def test_relaxation_oscillator_starts_and_reaches_both_output_rails():
    deck = (
        "B1 out 0 V=5*TANH(1e5*(V(plus)-V(cap)))\n"
        "R1 out plus 10k\nR2 plus 0 10k\n"
        "R3 out cap 10k\nC1 cap 0 100n\n.ic V(cap)=0.1"
    )
    points = simulate_spice(deck, "tran 10u 5m 0 10u uic", ["v(out)"])["points"]
    values = [point["v(out)"] for point in points]
    assert min(values) < -4.9
    assert max(values) > 4.9
    transitions = sum((a < 0) != (b < 0) for a, b in zip(values, values[1:]))
    assert transitions >= 3


@pytest.mark.parametrize(
    "deck",
    [
        "V1 1 0 1\n.control\nshell touch /tmp/nope\n.endc",
        "V1 1 0 1\n.include /etc/passwd",
        "V1 1 0 1\n.tran 1n 1",
        "V1 1 0 1\n.end",
    ],
)
def test_deck_cannot_supply_control_file_access_or_analysis(deck):
    with pytest.raises(SpiceError, match="not allowed"):
        simulate_spice(deck, "op", [])


@pytest.mark.parametrize("analysis", ["noise", "op extra", "tran 1m", "ac dec ten 1 10", "dc V1 0 1"])
def test_analysis_grammar_rejects_unsupported_or_incomplete_commands(analysis):
    with pytest.raises(SpiceError, match="analysis must"):
        simulate_spice("V1 1 0 1\nR1 1 0 1k", analysis, [])


def test_ac_point_count_is_bounded_before_ngspice_runs():
    with pytest.raises(SpiceError, match="cannot exceed"):
        simulate_spice("V1 1 0 AC 1\nR1 1 0 1k", "ac dec 10001 1 10", [])


def test_unknown_vector_is_actionable():
    with pytest.raises(SpiceError, match="unknown output"):
        simulate_spice("V1 1 0 1\nR1 1 0 1k", "op", ["v(missing)"])
