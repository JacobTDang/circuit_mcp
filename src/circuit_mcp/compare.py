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
        return ("volts" if kind == "dc" else "vrms"), _number(raw, f"measured[{label!r}]")
    if len(raw) != 1:
        raise CompareError(f"measured[{label!r}] must name exactly one of volts, vrms, vpp, vpk")
    ((basis, value),) = raw.items()
    allowed = ("volts",) if kind == "dc" else AC_BASES
    if basis not in allowed:
        raise CompareError(f"{label!r} is a {kind.upper()} probe; give its reading as {' or '.join(allowed)}")
    return basis, _number(value, f"measured[{label!r}][{basis!r}]")


def _close(a: float, b: float, tolerance_pct: float) -> bool:
    if abs(b) < ZERO_FLOOR_V:
        return abs(a - b) <= ABS_TOLERANCE_V
    return abs(a - b) <= abs(b) * tolerance_pct / 100


def _judge(expected: float, measured: float, tolerance_pct: float) -> dict[str, Any]:
    if abs(expected) < ZERO_FLOOR_V:
        return {"expected": expected, "measured": measured, "error_pct": None,
                "within_tolerance": abs(measured - expected) <= ABS_TOLERANCE_V}
    error = (measured - expected) / abs(expected) * 100
    return {"expected": expected, "measured": measured, "error_pct": round(error, 2),
            "within_tolerance": abs(error) <= tolerance_pct}


def _peak(basis: str, value: float) -> float:
    """Every reading as a peak (a signed level for DC): the basis predicted gains use."""
    return {"volts": value, "vpk": value, "vpp": value / 2, "vrms": value * math.sqrt(2)}[basis]


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


def _hint(probe: Probe, basis: str, measured: float, expected: float, sources: tuple[Source, ...],
          swing: dict[str, float], outputs: set[str], tolerance_pct: float) -> dict[str, str] | None:
    """The likeliest cause of an out-of-tolerance reading, or None if nothing fits."""
    if basis == "volts" and abs(expected) >= ZERO_FLOOR_V and _close(measured, -expected, tolerance_pct):
        return {"kind": "sign",
                "message": f"{probe.label} reads {_fmt(measured, basis)}: the right size with the wrong sign. "
                           "Check the probe polarity, or a minus sign lost when the value was written down."}
    for source in sources:
        setting = _source_setting(source, basis)
        if source.node == probe.node or setting is None or abs(setting) < ZERO_FLOOR_V:
            continue
        if _close(measured, setting, tolerance_pct):
            return {"kind": "source",
                    "message": f"{probe.label} reads {_fmt(measured, basis)}, which is source {source.ref}'s "
                               f"setting ({_fmt(setting, basis)}), not the {_fmt(expected, basis)} expected at "
                               f"{probe.node}. The probe is probably on {source.node} instead of {probe.node}."}
    if probe.node in outputs:
        if basis == "volts":
            on_rail = measured >= swing["high"] - RAIL_MARGIN_V or measured <= swing["low"] + RAIL_MARGIN_V
        elif basis in ("vpk", "vpp"):
            peak = measured if basis == "vpk" else measured / 2
            on_rail = peak >= min(swing["high"], -swing["low"]) - RAIL_MARGIN_V
        else:
            on_rail = False
        if on_rail:
            return {"kind": "rail",
                    "message": f"{probe.label} reads {_fmt(measured, basis)}, at the op amp's output limit "
                               f"({swing['low']:g} V to {swing['high']:g} V on this supply): the output is "
                               "clipping. Lower the input or the gain."}
    return None


def compare_readings(build: Any, measured: Any, tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> dict[str, Any]:
    """Each measured probe against the build's prediction, with the likeliest cause of every miss."""
    if not isinstance(measured, dict) or not measured:
        raise CompareError("measured must map at least one probe label to its reading")
    tolerance = _number(tolerance_pct, "tolerance_pct")
    if tolerance <= 0:
        raise CompareError("tolerance_pct must be positive")
    try:
        parsed = parse_build(build)
        predicted = expectations(build)
    except (BuildError, ExpectError) as exc:
        raise CompareError(f"cannot predict this build: {exc}") from exc
    probes = {probe.label: probe for probe in parsed.probes}
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
            hint = _hint(probes[label], basis, value, expected[basis], parsed.sources,
                         predicted["swing"], outputs, tolerance)
            if hint is not None:
                entry["hint"] = hint
        readings.append(entry)
        peaks[label] = _peak(basis, value)
    gains: list[dict[str, Any]] = []
    for gain in predicted["gains"]:
        out_peak, in_peak = peaks.get(gain["output"]), peaks.get(gain["input"])
        if out_peak is None or in_peak is None or abs(in_peak) < ZERO_FLOOR_V:
            continue
        gains.append({"output": gain["output"], "input": gain["input"],
                      **_judge(gain["gain"], round(out_peak / in_peak, 3), tolerance)})
    return {"ok": True, "tolerance_pct": tolerance, "readings": readings, "gains": gains,
            "all_within_tolerance": all(item["within_tolerance"] for item in readings + gains)}
