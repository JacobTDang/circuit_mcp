"""Measured bench readings against what the build says the bench should read.

``expectations`` predicts every probe from the same build the breadboard card
draws. This runs the other direction: given what the student actually read, it
says which probes disagree, by how much, and the likeliest reason, while the
circuit is still on the bench and a wrong probe can still be moved.
"""
from __future__ import annotations

import math
from typing import Any

from .breadboard import BuildError, Probe, Source, parse_build
from .expect import ExpectError, expectations

DEFAULT_TOLERANCE_PCT = 5.0
ZERO_FLOOR_V = 1e-3      # an expected value this close to zero has no meaningful percent error
ABS_TOLERANCE_V = 0.05   # the pass band used instead, in volts
RAIL_MARGIN_V = 0.1      # a reading this close to the chip's output limit is sitting on the rail
AC_BASES = ("vrms", "vpp", "vpk")
_UNITS = {"volts": "V", "vrms": "V RMS", "vpp": "V pk-pk", "vpk": "V peak"}


class CompareError(ValueError):
    """The build cannot be simulated, or the measurements do not fit it."""


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CompareError(f"{where} must be a finite number")
    return float(value)


def _fmt(value: float, basis: str) -> str:
    return f"{value:.4g} {_UNITS[basis]}"


def _reading(label: str, raw: Any, kind: str) -> tuple[str, float]:
    """(basis, value) for one measurement. A bare number is volts for DC, V RMS for AC."""
    if not isinstance(raw, dict):
        basis, value = ("volts" if kind == "dc" else "vrms"), _number(raw, f"measured[{label!r}]")
    else:
        if len(raw) != 1:
            raise CompareError(f"measured[{label!r}] must name exactly one of volts, vrms, vpp, vpk")
        ((basis, value),) = raw.items()
        allowed = ("volts",) if kind == "dc" else AC_BASES
        if basis not in allowed:
            raise CompareError(f"{label!r} is a {kind.upper()} probe; give its reading as {' or '.join(allowed)}")
        value = _number(value, f"measured[{label!r}][{basis!r}]")
    if basis in AC_BASES and value < 0:
        raise CompareError(f"{label!r} reads {_fmt(value, basis)}, but AC readings are magnitudes and cannot be "
                           "negative: an inverted output is the phase the expected card reports, not a minus sign")
    return basis, value


def _close(a: float, b: float, tolerance_pct: float) -> bool:
    if abs(b) < ZERO_FLOOR_V:
        return abs(a - b) <= ABS_TOLERANCE_V
    return abs(a - b) <= abs(b) * tolerance_pct / 100


def _judge(expected: float, measured: float, tolerance_pct: float) -> dict[str, Any]:
    error = None if abs(expected) < ZERO_FLOOR_V else round((measured - expected) / abs(expected) * 100, 2)
    return {"expected": expected, "measured": measured, "error_pct": error,
            "within_tolerance": _close(measured, expected, tolerance_pct)}


def _peak(basis: str, value: float, expected: dict[str, Any]) -> float:
    """A reading as a peak (a signed level for DC): the basis predicted gains use.

    An AC reading is scaled by the predicted waveform's own peak-to-basis ratio,
    so a square, a triangle or a clipped sine is not treated as a sine.
    """
    if basis == "volts":
        return value
    level = expected[basis]
    if abs(level) < ZERO_FLOOR_V:
        # A flat predicted trace has no shape to scale by, so assume a sine.
        return {"vpk": value, "vpp": value / 2, "vrms": value * math.sqrt(2)}[basis]
    return value * expected["vpk"] / level


def _probes_by_label(probes: tuple[Probe, ...]) -> dict[str, Probe]:
    """Each probe by its label. A repeated label would judge a reading against the wrong node."""
    labels = [probe.label for probe in probes]
    repeated = sorted({label for label in labels if labels.count(label) > 1})
    if repeated:
        raise CompareError(f"probe labels must be unique to compare readings; "
                           f"repeated: {', '.join(map(repr, repeated))}")
    return {probe.label: probe for probe in probes}


def _source_setting(source: Source, basis: str) -> float | None:
    """A source's setting in the basis the probe was read in, or None if not comparable."""
    if source.kind == "dc":
        return source.amplitude if basis == "volts" else None
    if basis == "volts":
        return None
    if source.kind == "sine":        # amplitude is V RMS
        rms = source.amplitude
        return {"vrms": rms, "vpk": rms * math.sqrt(2), "vpp": 2 * math.sqrt(2) * rms}[basis]
    vpp = source.amplitude           # square: amplitude is V pk-pk, and its RMS equals its peak
    return {"vpp": vpp, "vpk": vpp / 2, "vrms": vpp / 2}[basis]


_Fit = tuple[float, dict[str, str]]   # how far the reading is from the value a cause names, and that cause's hint


def _source_hint(probe: Probe, basis: str, measured: float, level: float, sources: tuple[Source, ...],
                 tolerance_pct: float) -> _Fit | None:
    """The reading is another source's setting (the nearest, if several match): the probe is on that node."""
    settings = [(source, _source_setting(source, basis)) for source in sources if source.node != probe.node]
    fits = [(abs(measured - setting), source, setting) for source, setting in settings
            if setting is not None and abs(setting) >= ZERO_FLOOR_V and _close(measured, setting, tolerance_pct)]
    if not fits:
        return None
    distance, source, setting = min(fits, key=lambda fit: fit[0])
    return distance, {"kind": "source",
                      "message": f"{probe.label} reads {_fmt(measured, basis)}, which is source {source.ref}'s "
                                 f"setting ({_fmt(setting, basis)}), not the {_fmt(level, basis)} expected at "
                                 f"{probe.node}. The probe is probably on {source.node} instead of {probe.node}."}


def _supply_hint(probe: Probe, basis: str, measured: float, swing: dict[str, float], vplus: float, vminus: float,
                 tolerance_pct: float) -> _Fit | None:
    """A DC reading out at a supply rail, past the op amp's swing: the probe is on the power bus, not the output."""
    if basis != "volts":
        return None
    if measured >= swing["high"] + RAIL_MARGIN_V and (measured >= vplus or _close(measured, vplus, tolerance_pct)):
        rail = vplus
    elif measured <= swing["low"] - RAIL_MARGIN_V and (measured <= vminus or _close(measured, vminus, tolerance_pct)):
        rail = vminus
    else:
        return None
    return abs(measured - rail), {"kind": "supply",
                                  "message": f"{probe.label} reads {_fmt(measured, basis)}, at the {rail:g} V supply "
                                             f"rail and past the op amp's output limit ({swing['low']:g} V to "
                                             f"{swing['high']:g} V on this supply). The probe is probably on the "
                                             f"supply rail instead of {probe.node}."}


def _within_swing(value: float, swing: dict[str, float]) -> bool:
    """Clear of both output limits by more than the margin that counts as sitting on one."""
    return swing["low"] + RAIL_MARGIN_V < value < swing["high"] - RAIL_MARGIN_V


def _saturation_hint(probe: Probe, basis: str, measured: float, expected: dict[str, Any],
                     swing: dict[str, float]) -> _Fit | None:
    """The output is on the op amp's limit, with advice that depends on whether the build predicts it there.

    Past the limit counts as on it: a chip's swing is a typical figure, and an RMS
    reading scaled as the predicted shape overstates a clipped trace's peak.
    """
    if basis == "volts":
        if _within_swing(measured, swing):
            return None
        distance = min(abs(measured - swing["high"]), abs(measured - swing["low"]))
        predicted_there = not _within_swing(expected["volts"], swing)
    else:
        peak = _peak(basis, measured, expected)
        if _within_swing(expected["mean"] + peak, swing) and _within_swing(expected["mean"] - peak, swing):
            return None
        distance, predicted_there = 0.0, True   # a trace has no single value to be near: on the limit is exact
    # Too much input or gain only explains a limit the build already reaches. An output predicted well inside
    # its swing that sits on a limit has an open or miswired feedback path, and lowering the gain will not help.
    advice = ("clipping. Lower the input or the gain." if predicted_there
              else "saturated. Check the feedback wiring first.")
    return distance, {"kind": "rail",
                      "message": f"{probe.label} reads {_fmt(measured, basis)}, at the op amp's output limit "
                                 f"({swing['low']:g} V to {swing['high']:g} V on this supply): the output is {advice}"}


def _hint(probe: Probe, basis: str, measured: float, expected: dict[str, Any], sources: tuple[Source, ...],
          swing: dict[str, float], outputs: set[str], tolerance_pct: float,
          vplus: float, vminus: float) -> dict[str, str] | None:
    """The likeliest cause of an out-of-tolerance reading, or None if nothing fits."""
    level = expected[basis]
    if basis == "volts" and abs(level) >= ZERO_FLOOR_V and _close(measured, -level, tolerance_pct):
        return {"kind": "sign",
                "message": f"{probe.label} reads {_fmt(measured, basis)}: the right size with the wrong sign. "
                           "Check the probe polarity, or a minus sign lost when the value was written down."}
    candidates = []
    if probe.node in outputs:
        candidates += [_saturation_hint(probe, basis, measured, expected, swing),
                       _supply_hint(probe, basis, measured, swing, vplus, vminus, tolerance_pct)]
    candidates.append(_source_hint(probe, basis, measured, level, sources, tolerance_pct))
    fits = [fit for fit in candidates if fit is not None]
    # More than one cause can fit: a single-supply buffer saturated at 14.9 V is also within 5% of its 15 V
    # source, and an LM324 output past its typical limit may be nearer that limit or the supply rail. The cause
    # whose value the reading is closer to is the likelier one.
    return min(fits, key=lambda fit: fit[0])[1] if fits else None


def compare_readings(build: Any, measured: Any, tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> dict[str, Any]:
    """Each measured probe against the build's prediction, with the likeliest cause of every miss.

    A predicted gain is checked only when both of its probes were measured. One
    whose measured input reads below 1 mV is returned as failed, with the reason.
    """
    if not isinstance(measured, dict) or not measured:
        raise CompareError("measured must map at least one probe label to its reading")
    tolerance = _number(tolerance_pct, "tolerance_pct")
    if tolerance <= 0:
        raise CompareError("tolerance_pct must be positive")
    try:
        parsed = parse_build(build)
        probes = _probes_by_label(parsed.probes)
        predicted = expectations(build)
    except (BuildError, ExpectError) as exc:
        raise CompareError(f"cannot predict this build: {exc}") from exc
    expected_by_label = {reading["label"]: reading for reading in predicted["readings"]}
    unknown = [label for label in measured if label not in expected_by_label]
    if unknown:
        raise CompareError(f"no probe labelled {', '.join(map(repr, unknown))}; "
                           f"this build's probes are {', '.join(map(repr, expected_by_label))}")
    outputs = {use.out for use in parsed.opamps}
    readings: list[dict[str, Any]] = []
    peaks: dict[str, float] = {}
    for label, raw in measured.items():
        expected = expected_by_label[label]
        basis, value = _reading(label, raw, expected["kind"])
        entry = {"label": label, "basis": basis, **_judge(expected[basis], value, tolerance)}
        if not entry["within_tolerance"]:
            hint = _hint(probes[label], basis, value, expected, parsed.sources,
                         predicted["swing"], outputs, tolerance, parsed.vplus, parsed.vminus)
            if hint is not None:
                entry["hint"] = hint
        readings.append(entry)
        peaks[label] = _peak(basis, value, expected)
    gains: list[dict[str, Any]] = []
    for gain in predicted["gains"]:
        out_peak, in_peak = peaks.get(gain["output"]), peaks.get(gain["input"])
        if out_peak is None or in_peak is None:
            continue
        pair = {"output": gain["output"], "input": gain["input"]}
        if abs(in_peak) < ZERO_FLOOR_V:
            gains.append({**pair, "expected": gain["gain"], "measured": None, "error_pct": None,
                          "within_tolerance": False,
                          "reason": f"{gain['input']} reads below 1 mV, so no gain can be compared"})
            continue
        gains.append({**pair, **_judge(gain["gain"], round(out_peak / in_peak, 3), tolerance)})
    return {"ok": True, "tolerance_pct": tolerance, "readings": readings, "gains": gains,
            "all_within_tolerance": all(item["within_tolerance"] for item in readings + gains)}
