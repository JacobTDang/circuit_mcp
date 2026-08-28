"""Known-answer tests for system, converter, and spectral characterization."""
from __future__ import annotations

import math

import numpy as np
import pytest
import sympy as sp

from circuit_mcp.course_metrics import (
    alias_frequency,
    bjt_emitter_follower,
    MetricsError,
    converter_metrics,
    dac_output,
    rectifier_metrics,
    relaxation_oscillator,
    spectrum_metrics,
    transfer_metrics,
    quantize,
    transimpedance,
)


def test_first_order_transfer_characterization_matches_textbook_values():
    s = sp.Symbol("s")
    result = transfer_metrics(1 / (s + 1), {})
    assert result["stable"] is True
    assert result["poles"] == [{"real": -1.0, "imag": 0.0}]
    assert result["zeros"] == []
    assert result["dc_gain"] == {"real": 1.0, "imag": 0.0}
    # python-control locates the -3 dB crossing on a generated frequency grid.
    assert result["bandwidth_rad_s"] == pytest.approx(1.0, abs=0.003)
    assert result["step"]["SteadyStateValue"] == pytest.approx(1.0)


def test_parameter_substitution_and_unstable_pole_detection():
    s, tau = sp.symbols("s tau")
    stable = transfer_metrics(2 / (tau * s + 1), {"tau": 0.5})
    assert stable["poles"][0]["real"] == pytest.approx(-2)
    assert stable["dc_gain"]["real"] == pytest.approx(2)
    unstable = transfer_metrics(1 / (s - 1), {})
    assert unstable["stable"] is False
    assert unstable["stability_classification"] == "unstable"
    assert unstable["step"] is None


def test_stability_classification_distinguishes_origin_and_repeated_axis_poles():
    s = sp.Symbol("s")
    marginal = transfer_metrics(1 / s, {})
    assert marginal["stable"] is False
    assert marginal["stability_classification"] == "marginally_stable"
    repeated = transfer_metrics(1 / s**2, {})
    assert repeated["stability_classification"] == "unstable"


def test_loop_transfer_reports_independently_known_phase_margin():
    s = sp.Symbol("s")
    result = transfer_metrics(10 / (s * (s + 1)), {})
    assert result["phase_margin_deg"] == pytest.approx(17.964, abs=0.002)
    assert result["gain_crossover_rad_s"] == pytest.approx(3.084, abs=0.002)


def test_ideal_adc_and_dac_have_zero_endpoint_linearity_error():
    dac = converter_metrics("dac", 2, [0, 0.25, 0.5, 0.75], 0, 1)
    assert dac["max_abs_inl_lsb"] == pytest.approx(0)
    assert dac["max_abs_dnl_lsb"] == pytest.approx(0)
    adc = converter_metrics("adc", 2, [0.25, 0.5, 0.75], 0, 1)
    assert adc["max_abs_inl_lsb"] == pytest.approx(0)
    assert adc["max_abs_dnl_lsb"] == pytest.approx(0)


def test_adc_detects_a_missing_code_from_coincident_transitions():
    result = converter_metrics("adc", 2, [0.25, 0.25, 0.75], 0, 1)
    assert result["missing_codes"] == [1]
    assert result["monotonic"] is True
    assert result["nondecreasing"] is True
    assert result["strictly_increasing"] is False
    assert "duplicate" in result["monotonic_definition"]
    assert result["dnl_lsb"][1] == -1


def test_coherent_spectrum_recovers_harmonics_thd_sinad_and_enob():
    count, sample_rate, fundamental = 1024, 1024.0, 64.0
    time = np.arange(count) / sample_rate
    samples = 0.2 + np.sin(2 * np.pi * fundamental * time) + 0.1 * np.sin(4 * np.pi * fundamental * time)
    result = spectrum_metrics(samples.tolist(), sample_rate, fundamental, 5)
    assert result["dc"] == pytest.approx(0.2, abs=1e-12)
    assert result["harmonics"][0]["amplitude_peak"] == pytest.approx(1, abs=1e-12)
    assert result["harmonics"][1]["amplitude_peak"] == pytest.approx(0.1, abs=1e-12)
    assert result["thd_ratio"] == pytest.approx(0.1, abs=1e-12)
    assert result["sinad_db"] == pytest.approx(20, abs=1e-10)
    assert result["enob"] == pytest.approx((20 - 1.76) / 6.02)


def test_spectrum_refuses_leakage_prone_noncoherent_record():
    with pytest.raises(MetricsError, match="coherent"):
        spectrum_metrics([0.0] * 16, 16, 1.1, 5)


def test_ideal_three_bit_quantizer_codes_boundaries_clipping_and_error():
    result = quantize([-0.1, 0, 0.124, 0.125, 0.999, 1.0], 3, 0, 1)
    assert result["codes"] == [0, 0, 0, 1, 7, 7]
    assert result["binary_codes"] == ["000", "000", "000", "001", "111", "111"]
    assert result["lsb"] == 0.125
    assert result["clipped"] == [True, False, False, False, False, True]
    assert abs(result["quantization_error"][2]) <= result["lsb"] / 2


def test_remaining_ee230_closed_form_helpers_match_independent_answers():
    rectifier = rectifier_metrics(5, 0)
    assert rectifier["average_dc_v"] == pytest.approx(5 / math.pi)
    follower = bjt_emitter_follower(0.002, 100, 0.025, 1000)
    assert follower["gm_s"] == pytest.approx(0.08)
    assert follower["r_pi_ohm"] == pytest.approx(1250)
    assert follower["voltage_gain"] == pytest.approx(101000 / 102250)
    oscillator = relaxation_oscillator(5, 2, 0.001)
    expected_period = 0.002 * math.log(7 / 3)
    assert oscillator["period_s"] == pytest.approx(expected_period)
    assert oscillator["frequency_hz"] == pytest.approx(1 / expected_period)
    dac = dac_output([0, 3, 5, 7], 3, 0, 8)
    assert dac["outputs_v"] == [0, 3, 5, 7]
    assert alias_frequency(1300, 1000)["alias_hz"] == pytest.approx(300)
    tia = transimpedance(20e-6, 100e3)
    assert tia["output_v"] == pytest.approx(-2)


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (rectifier_metrics, (-1, 0)),
        (bjt_emitter_follower, (0, 100, 0.025, 1000)),
        (relaxation_oscillator, (5, 5, 0.001)),
        (dac_output, ([8], 3, 0, 8)),
        (alias_frequency, (1, 0)),
        (transimpedance, (1e-6, 0)),
    ],
)
def test_closed_form_helpers_reject_unphysical_inputs(function, args):
    with pytest.raises(MetricsError):
        function(*args)
