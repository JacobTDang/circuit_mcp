"""Deterministic circuit-system, converter, and spectral measurements."""
from __future__ import annotations

import math
from typing import Any

import control
import numpy as np
import sympy as sp


class MetricsError(ValueError):
    """Inputs do not define a meaningful bounded course metric."""


def _complex(value: complex) -> dict[str, float]:
    return {"real": float(value.real), "imag": float(value.imag)}


def transfer_metrics(expr: sp.Expr, parameters: dict[str, float]) -> dict[str, Any]:
    """Numeric poles/zeros, gains, stability margins, bandwidth, and step data."""
    names = {str(symbol): symbol for symbol in expr.free_symbols}
    unknown = sorted(set(parameters) - set(names))
    if unknown:
        raise MetricsError(f"parameter(s) not present in expression: {unknown}")
    substitutions = {names[name]: float(value) for name, value in parameters.items()}
    numeric = sp.cancel(expr.subs(substitutions))
    remaining = [symbol for symbol in numeric.free_symbols if str(symbol) != "s"]
    if remaining:
        raise MetricsError(
            f"numeric characterization needs values for: {sorted(map(str, remaining))}"
        )
    s_candidates = [symbol for symbol in numeric.free_symbols if str(symbol) == "s"]
    s = s_candidates[0] if s_candidates else sp.Symbol("s")
    numerator, denominator = map(sp.Poly, sp.fraction(numeric), (s, s))
    try:
        num = [float(value) for value in numerator.all_coeffs()]
        den = [float(value) for value in denominator.all_coeffs()]
    except (TypeError, ValueError) as exc:
        raise MetricsError("transfer function coefficients must be finite real numbers") from exc
    system = control.TransferFunction(num, den)
    system_poles = np.asarray(control.poles(system), dtype=complex)
    system_zeros = np.asarray(control.zeros(system), dtype=complex)
    gain_margin, phase_margin, phase_cross, gain_cross = control.margin(system)
    try:
        bandwidth = float(control.bandwidth(system))
    except Exception:
        bandwidth = math.nan
    dc_gain = complex(control.dcgain(system))
    tolerance = 1e-9
    right_half_plane = np.any(system_poles.real > tolerance)
    imaginary_axis = np.abs(system_poles.real) <= tolerance
    # A bounded-input marginal classification requires every imaginary-axis
    # pole to be simple. Numeric root finding may split a repeated pole, so
    # group roots within a scale-aware tolerance before classifying it.
    repeated_axis_pole = any(
        imaginary_axis[i]
        and any(
            i != j and abs(system_poles[i] - system_poles[j]) <= 1e-7 * max(1.0, abs(system_poles[i]))
            for j in range(len(system_poles))
        )
        for i in range(len(system_poles))
    )
    if right_half_plane or repeated_axis_pole:
        stability = "unstable"
    elif np.any(imaginary_axis):
        stability = "marginally_stable"
    else:
        stability = "asymptotically_stable"
    metrics: dict[str, Any] = {
        "ok": True,
        "poles": [_complex(value) for value in system_poles],
        "zeros": [_complex(value) for value in system_zeros],
        "stable": stability == "asymptotically_stable",
        "stability_classification": stability,
        "dc_gain": _complex(dc_gain),
        "gain_margin": None if not math.isfinite(float(gain_margin)) else float(gain_margin),
        "gain_margin_db": None if not math.isfinite(float(gain_margin)) else float(20 * math.log10(gain_margin)),
        "phase_margin_deg": None if not math.isfinite(float(phase_margin)) else float(phase_margin),
        "phase_crossover_rad_s": None if not math.isfinite(float(phase_cross)) else float(phase_cross),
        "gain_crossover_rad_s": None if not math.isfinite(float(gain_cross)) else float(gain_cross),
        "bandwidth_rad_s": None if not math.isfinite(bandwidth) else bandwidth,
    }
    # Step metrics are meaningful only for a proper, stable transfer function.
    if len(num) <= len(den) and metrics["stable"]:
        try:
            info = control.step_info(system)
            metrics["step"] = {
                key: (None if not math.isfinite(float(value)) else float(value))
                for key, value in info.items()
            }
        except Exception:
            metrics["step"] = None
    else:
        metrics["step"] = None
    return metrics


def converter_metrics(
    kind: str, bits: int, values: list[float], v_min: float, v_max: float
) -> dict[str, Any]:
    """Endpoint INL/DNL for a complete DAC level set or ADC transition set."""
    if not 1 <= bits <= 16:
        raise MetricsError("bits must be between 1 and 16")
    if not math.isfinite(v_min) or not math.isfinite(v_max) or v_max <= v_min:
        raise MetricsError("v_max must be finite and greater than v_min")
    levels = 1 << bits
    data = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(data)) or np.any(np.diff(data) < 0):
        raise MetricsError("values must be finite and nondecreasing")
    lsb = (v_max - v_min) / levels
    if kind == "dac":
        if len(data) != levels:
            raise MetricsError(f"a {bits}-bit DAC requires {levels} output levels")
        if data[-1] <= data[0]:
            raise MetricsError("DAC endpoint levels must have nonzero span")
        # Endpoint gain removes offset and gain error before linearity grading.
        ideal = np.linspace(data[0], data[-1], levels)
        fitted_lsb = (data[-1] - data[0]) / (levels - 1)
        inl = (data - ideal) / fitted_lsb
        dnl = np.diff(data) / fitted_lsb - 1
    elif kind == "adc":
        if len(data) != levels - 1:
            raise MetricsError(f"a {bits}-bit ADC requires {levels - 1} transition levels")
        ideal = v_min + np.arange(1, levels) * lsb
        inl = (data - ideal) / lsb
        widths = np.diff(np.concatenate(([v_min], data, [v_max])))
        dnl = widths / lsb - 1
    else:
        raise MetricsError("kind must be 'adc' or 'dac'")
    return {
        "ok": True,
        "kind": kind,
        "bits": bits,
        "lsb": float(lsb),
        "inl_lsb": inl.tolist(),
        "dnl_lsb": dnl.tolist(),
        "max_abs_inl_lsb": float(np.max(np.abs(inl))),
        "max_abs_dnl_lsb": float(np.max(np.abs(dnl))),
        "missing_codes": [int(index) for index in np.flatnonzero(dnl <= -1)],
        # Preserve the original field for compatibility, but spell out that
        # converter monotonicity permits coincident levels/transitions.
        "monotonic": bool(np.all(dnl >= -1)),
        "monotonic_definition": "nondecreasing transfer; duplicate levels are allowed",
        "nondecreasing": bool(np.all(np.diff(data) >= 0)),
        "strictly_increasing": bool(np.all(np.diff(data) > 0)),
    }


def spectrum_metrics(
    samples: list[float], sample_rate: float, fundamental_hz: float, harmonics: int
) -> dict[str, Any]:
    """Coherent FFT amplitudes, THD, SINAD, and ENOB from real samples."""
    data = np.asarray(samples, dtype=float)
    if not 16 <= len(data) <= 131_072 or not np.all(np.isfinite(data)):
        raise MetricsError("samples must contain 16 to 131,072 finite values")
    if sample_rate <= 0 or fundamental_hz <= 0 or fundamental_hz >= sample_rate / 2:
        raise MetricsError("fundamental_hz must lie strictly between DC and Nyquist")
    if not 1 <= harmonics <= 20:
        raise MetricsError("harmonics must be between 1 and 20")
    exact_bin = fundamental_hz * len(data) / sample_rate
    fundamental_bin = int(round(exact_bin))
    if not math.isclose(exact_bin, fundamental_bin, abs_tol=1e-9):
        raise MetricsError("fundamental must be coherent with the sample record")
    spectrum = np.fft.rfft(data)
    amplitudes = 2 * np.abs(spectrum) / len(data)
    amplitudes[0] /= 2
    if len(data) % 2 == 0:
        amplitudes[-1] /= 2
    harmonic_rows = []
    harmonic_power = 0.0
    for order in range(1, harmonics + 1):
        index = order * fundamental_bin
        if index >= len(amplitudes):
            break
        amplitude = float(amplitudes[index])
        harmonic_rows.append({"order": order, "frequency_hz": order * fundamental_hz, "amplitude_peak": amplitude})
        if order > 1:
            harmonic_power += amplitude * amplitude
    fundamental = float(amplitudes[fundamental_bin])
    if fundamental == 0:
        raise MetricsError("fundamental amplitude is zero")
    thd = math.sqrt(harmonic_power) / fundamental
    all_power = float(np.sum(amplitudes[1:] ** 2))
    noise_distortion = max(0.0, all_power - fundamental * fundamental)
    sinad = math.inf if noise_distortion == 0 else 10 * math.log10(fundamental * fundamental / noise_distortion)
    return {
        "ok": True,
        "sample_count": len(data),
        "dc": float(amplitudes[0]),
        "harmonics": harmonic_rows,
        "thd_ratio": thd,
        "thd_percent": 100 * thd,
        "thd_db": None if thd == 0 else 20 * math.log10(thd),
        "sinad_db": None if not math.isfinite(sinad) else sinad,
        "enob": None if not math.isfinite(sinad) else (sinad - 1.76) / 6.02,
    }


def quantize(
    values: list[float], bits: int, v_min: float, v_max: float
) -> dict[str, Any]:
    """Ideal unipolar ADC codes, bin-center reconstructions, and errors."""
    if not 1 <= bits <= 24:
        raise MetricsError("bits must be between 1 and 24")
    if not math.isfinite(v_min) or not math.isfinite(v_max) or v_max <= v_min:
        raise MetricsError("v_max must be finite and greater than v_min")
    data = np.asarray(values, dtype=float)
    if len(data) > 100_000 or not np.all(np.isfinite(data)):
        raise MetricsError("values must contain at most 100,000 finite numbers")
    levels = 1 << bits
    lsb = (v_max - v_min) / levels
    unclipped = np.floor((data - v_min) / lsb).astype(np.int64)
    codes = np.clip(unclipped, 0, levels - 1)
    reconstructed = v_min + (codes + 0.5) * lsb
    return {
        "ok": True,
        "bits": bits,
        "lsb": float(lsb),
        "codes": codes.tolist(),
        "binary_codes": [format(int(code), f"0{bits}b") for code in codes],
        "reconstructed": reconstructed.tolist(),
        "quantization_error": (data - reconstructed).tolist(),
        "clipped": ((data < v_min) | (data >= v_max)).tolist(),
    }


def opamp_limits(
    gain: float,
    noise_gain: float,
    gbw_hz: float,
    slew_rate_v_s: float,
    output_peak_v: float,
    signal_hz: float,
) -> dict[str, Any]:
    """First-order bandwidth and large-signal slew limits for an op-amp stage."""
    inputs = (gain, noise_gain, gbw_hz, slew_rate_v_s, output_peak_v, signal_hz)
    if not all(math.isfinite(value) for value in inputs):
        raise MetricsError("all op-amp parameters must be finite")
    if noise_gain < 1 or gbw_hz <= 0 or slew_rate_v_s <= 0 or output_peak_v < 0 or signal_hz < 0:
        raise MetricsError("noise_gain must be >= 1 and rates/amplitudes must be nonnegative")
    bandwidth = gbw_hz / noise_gain
    full_power = math.inf if output_peak_v == 0 else slew_rate_v_s / (2 * math.pi * output_peak_v)
    required_slew = 2 * math.pi * signal_hz * output_peak_v
    return {
        "ok": True,
        "closed_loop_gain": gain,
        "noise_gain": noise_gain,
        "closed_loop_bandwidth_hz": bandwidth,
        "full_power_bandwidth_hz": None if not math.isfinite(full_power) else full_power,
        "required_slew_rate_v_s": required_slew,
        "bandwidth_limited": signal_hz > bandwidth,
        "slew_limited": required_slew > slew_rate_v_s,
        "small_signal_available": signal_hz <= bandwidth,
        "large_signal_available": required_slew <= slew_rate_v_s,
    }


def rectifier_metrics(input_peak_v: float, diode_drop_v: float = 0.0) -> dict[str, Any]:
    """Constant-drop half-wave rectifier conduction angle and DC average."""
    if not all(math.isfinite(v) for v in (input_peak_v, diode_drop_v)):
        raise MetricsError("rectifier values must be finite")
    if input_peak_v <= 0 or diode_drop_v < 0:
        raise MetricsError("input_peak_v must be positive and diode_drop_v nonnegative")
    if diode_drop_v >= input_peak_v:
        return {"ok": True, "conducts": False, "output_peak_v": 0.0, "average_dc_v": 0.0}
    angle = math.asin(diode_drop_v / input_peak_v)
    average = (
        2 * input_peak_v * math.cos(angle)
        - diode_drop_v * (math.pi - 2 * angle)
    ) / (2 * math.pi)
    return {
        "ok": True,
        "conducts": True,
        "conduction_start_rad": angle,
        "conduction_end_rad": math.pi - angle,
        "output_peak_v": input_peak_v - diode_drop_v,
        "average_dc_v": average,
        "piecewise": "max(input_peak_v*sin(phase)-diode_drop_v, 0)",
    }


def bjt_emitter_follower(
    collector_current_a: float, beta: float, thermal_voltage_v: float, load_ohm: float
) -> dict[str, Any]:
    """Hybrid-pi emitter-follower gain with r_o neglected."""
    values = (collector_current_a, beta, thermal_voltage_v, load_ohm)
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise MetricsError("BJT current, beta, thermal voltage, and load must be positive")
    gm = collector_current_a / thermal_voltage_v
    r_pi = beta / gm
    emitter_factor = (beta + 1) * load_ohm
    return {
        "ok": True,
        "gm_s": gm,
        "r_pi_ohm": r_pi,
        "voltage_gain": emitter_factor / (r_pi + emitter_factor),
        "assumptions": ["forward active", "hybrid-pi", "r_o neglected", "unloaded base source"],
    }


def relaxation_oscillator(rail_v: float, threshold_v: float, rc_s: float) -> dict[str, Any]:
    """Symmetric Schmitt-trigger RC oscillator period and frequency."""
    if not all(math.isfinite(v) and v > 0 for v in (rail_v, threshold_v, rc_s)):
        raise MetricsError("rail, threshold, and RC must be positive")
    if threshold_v >= rail_v:
        raise MetricsError("threshold_v must be below rail_v")
    half_period = rc_s * math.log((rail_v + threshold_v) / (rail_v - threshold_v))
    period = 2 * half_period
    return {"ok": True, "half_period_s": half_period, "period_s": period, "frequency_hz": 1 / period}


def dac_output(
    codes: list[int], bits: int, v_min: float = 0.0, v_max: float = 1.0
) -> dict[str, Any]:
    """Ideal straight-binary DAC output using a span/2**bits LSB."""
    if not 1 <= bits <= 24:
        raise MetricsError("bits must be between 1 and 24")
    if not math.isfinite(v_min) or not math.isfinite(v_max) or v_max <= v_min:
        raise MetricsError("v_max must be finite and greater than v_min")
    levels = 1 << bits
    if len(codes) > 100_000 or any(type(code) is not int or not 0 <= code < levels for code in codes):
        raise MetricsError(f"codes must be integers from 0 through {levels - 1}")
    lsb = (v_max - v_min) / levels
    return {
        "ok": True,
        "bits": bits,
        "lsb": lsb,
        "codes": codes,
        "binary_codes": [format(code, f"0{bits}b") for code in codes],
        "outputs_v": [v_min + code * lsb for code in codes],
    }


def alias_frequency(input_hz: float, sample_rate_hz: float) -> dict[str, Any]:
    """Fold a real sinusoid into the first Nyquist zone."""
    if not math.isfinite(input_hz) or input_hz < 0 or not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise MetricsError("input_hz must be nonnegative and sample_rate_hz positive")
    remainder = input_hz % sample_rate_hz
    alias = min(remainder, sample_rate_hz - remainder)
    return {"ok": True, "alias_hz": alias, "nyquist_hz": sample_rate_hz / 2}


def transimpedance(input_current_a: float, feedback_ohm: float) -> dict[str, Any]:
    """Ideal inverting current-to-voltage stage for current into the summing node."""
    if not math.isfinite(input_current_a) or not math.isfinite(feedback_ohm) or feedback_ohm <= 0:
        raise MetricsError("current must be finite and feedback_ohm positive")
    return {
        "ok": True,
        "transimpedance_ohm": -feedback_ohm,
        "output_v": -input_current_a * feedback_ohm,
        "current_direction": "positive current flows into the inverting summing node",
    }
