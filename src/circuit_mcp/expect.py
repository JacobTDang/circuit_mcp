"""What the bench should read, from the same build the breadboard was drawn from.

Numbers come from ngspice with a rail-limited op amp, so clipping shows up
where the real chip clips. Everything else about the op amp is ideal, which is
the right model for a first lab: measured gains land within resistor tolerance
of these, and a big miss means a wiring error, not a modelling one.
"""
from __future__ import annotations

import math
from typing import Any

from . import parts as P
from . import spice
from .breadboard import GROUND, Build, parse_build

SETTLE_PERIODS = 2
CAPTURE_PERIODS = 2
STEPS_PER_PERIOD = 200
SPARK_POINTS = 120
MAX_STEPS = 80_000


class ExpectError(ValueError):
    """The build cannot be simulated, or the simulation did not produce readings."""


def _spice_node(net: str) -> str:
    return "0" if net == GROUND else net


def deck(build: Build) -> str:
    """The ngspice netlist for a build. Deterministic, and returned to the caller."""
    lines = [P.opamp_subcircuit(),
             f"Vplus vplus 0 dc {build.vplus:g}",
             f"Vminus vminus 0 dc {build.vminus:g}"]
    led_is = P.led_saturation_current()
    lines.append(f".model led D(IS={led_is:.3e} N=2)")
    for part in build.parts:
        nodes = [_spice_node(n) for n in part.nodes]
        if part.kind in P.TWO_TERMINAL:
            lines.append(f"{P.TWO_TERMINAL[part.kind]}{part.ref} {nodes[0]} {nodes[1]} {P.parse_value(part.value):g}")
        elif part.kind == "led":
            lines.append(f"D{part.ref} {nodes[0]} {nodes[1]} led")
        elif part.kind == "pot":
            total = P.parse_value(part.value)
            pos = build.pot_positions[part.ref]
            upper, lower = max(1.0, total * (1 - pos)), max(1.0, total * pos)
            lines.append(f"R{part.ref}a {nodes[0]} {nodes[1]} {upper:g}")
            lines.append(f"R{part.ref}b {nodes[1]} {nodes[2]} {lower:g}")
    for use in build.opamps:
        chip = build.chips[use.chip]
        minus = "vminus" if build.dual_supply else "0"
        lines.append(f"X{use.ref} {_spice_node(use.inp)} {_spice_node(use.inn)} {_spice_node(use.out)} vplus {minus} railamp "
                     f"hi={chip.headroom_high:g} lo={chip.headroom_low:g}")
    for source in build.sources:
        node = _spice_node(source.node)
        if source.kind == "dc":
            lines.append(f"V{source.ref} {node} 0 dc {source.amplitude:g}")
        elif source.kind == "sine":
            lines.append(f"V{source.ref} {node} 0 dc 0 SIN(0 {source.amplitude * math.sqrt(2):.6g} {source.freq:g})")
        else:
            half, period = source.amplitude / 2, 1 / source.freq
            edge = period / 1000
            lines.append(f"V{source.ref} {node} 0 dc 0 PULSE({-half:g} {half:g} 0 {edge:.4g} {edge:.4g} {period / 2 - edge:.6g} {period:.6g})")
    return "\n".join(lines)


def _sparkline(samples: list[float], lo: float, hi: float) -> str:
    """A 240x60 polyline of the captured window, scaled to the chip's swing."""
    step = max(1, len(samples) // SPARK_POINTS)
    pts = samples[::step]
    span = (hi - lo) or 1.0
    coords = " ".join(f"{i * 240 / max(1, len(pts) - 1):.1f},{60 - (v - lo) / span * 60:.1f}" for i, v in enumerate(pts))
    zero = 60 - (0 - lo) / span * 60
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 60" width="240" height="60" role="img">'
            f'<line x1="0" y1="{zero:.1f}" x2="240" y2="{zero:.1f}" stroke="#666" stroke-width="0.5"/>'
            f'<polyline points="{coords}" fill="none" stroke="#4aa3df" stroke-width="1.5"/></svg>')


def _settle_time(build: Build, period: float) -> tuple[float, float]:
    """Long enough for the slowest RC in the build to reach steady state.

    ngspice's operating point sees each source at its t=0 value, so an
    integrator starts pinned at a rail and needs five of its own time constants
    to come back, not two periods of the input. The time constant comes back
    with the window so the caller can name it when the window is unusable.
    """
    resistances = [P.parse_value(p.value) for p in build.parts if p.kind in ("resistor", "pot")]
    capacitances = [P.parse_value(p.value) for p in build.parts if p.kind == "capacitor"]
    tau = max(resistances, default=0.0) * max(capacitances, default=0.0)
    return max(SETTLE_PERIODS * period, 5 * tau), tau


def _phase(correlation: float) -> str:
    """What the scope shows: same shape, flipped, or a quarter period apart."""
    if correlation > 0.3:
        return "in phase"
    if correlation < -0.3:
        return "inverted"
    return "quadrature (about 90 degrees)"


def _swing(build: Build) -> tuple[float, float]:
    if not build.chips:
        return build.vminus, build.vplus
    chip = next(iter(build.chips.values()))
    return build.vminus + chip.headroom_low, build.vplus - chip.headroom_high


def expectations(content: Any) -> dict[str, Any]:
    build = parse_build(content)
    if not build.probes:
        raise ExpectError("add at least one probe to say which nodes to read")
    if not build.sources:
        raise ExpectError("add at least one source; nothing drives this build")
    netlist = deck(build)
    ac = [s for s in build.sources if s.kind != "dc"]
    outputs = [f"v({_spice_node(p.node)})" for p in build.probes]
    lo_limit, hi_limit = _swing(build)
    opamp_outputs = {u.out for u in build.opamps}
    if ac:
        freq = min(s.freq for s in ac)
        period = 1 / freq
        settle, tau = _settle_time(build, period)
        stop = settle + CAPTURE_PERIODS * period
        step = period / STEPS_PER_PERIOD
        if stop / step > MAX_STEPS:
            raise ExpectError(f"the slowest RC in this build (tau = {tau:.3g} s) needs a {settle:.3g} s settle "
                              f"window, which cannot be resolved at {freq:g} Hz within the simulation budget; "
                              f"lower the RC product or the frequency")
        analysis = f"tran {step:.6g} {stop:.6g} {settle:.6g}"
    else:
        analysis = "op"
    try:
        result = spice.simulate_spice(netlist, analysis, outputs)
    except spice.SpiceError as exc:
        raise ExpectError(f"simulation failed: {exc}") from exc
    points = result["points"]
    if not points:
        raise ExpectError("simulation returned no points")
    samples: dict[str, list[float]] = {}
    for probe in build.probes:
        if probe.node not in samples:
            key = f"v({_spice_node(probe.node)})".lower()
            samples[probe.node] = [float(row[key]) for row in points]
    correlation: dict[tuple[str, str], float] = {}
    if ac:
        for a in build.probes:
            for b in build.probes:
                xa, xb = samples[a.node], samples[b.node]
                ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
                num = sum((u - ma) * (v - mb) for u, v in zip(xa, xb))
                den = math.sqrt(sum((u - ma) ** 2 for u in xa) * sum((v - mb) ** 2 for v in xb)) or 1.0
                correlation[(a.node, b.node)] = num / den   # -1..1
    readings: list[dict[str, Any]] = []
    levels: list[float] = []   # the unrounded amplitude behind each reading, which the gains divide
    for probe in build.probes:
        trace = samples[probe.node]
        if ac:
            vmax, vmin = max(trace), min(trace)
            vpk = (vmax - vmin) / 2
            mean = sum(trace) / len(trace)
            vrms = math.sqrt(sum((v - mean) ** 2 for v in trace) / len(trace))
            clipped = probe.node in opamp_outputs and (vmax >= hi_limit - 0.05 or vmin <= lo_limit + 0.05)
            readings.append({"label": probe.label, "node": probe.node, "role": probe.role, "kind": "ac",
                             "vpp": round(vmax - vmin, 4), "vpk": round(vpk, 4), "vrms": round(vrms, 4),
                             "mean": round(mean, 4), "clipped": clipped,
                             "sparkline": _sparkline(trace, min(lo_limit, vmin), max(hi_limit, vmax))})
            levels.append(vpk)
        else:
            readings.append({"label": probe.label, "node": probe.node, "role": probe.role, "kind": "dc",
                             "volts": round(trace[0], 4)})
            levels.append(trace[0])
    gains = []
    inputs = [(i, r) for i, r in enumerate(readings) if r["role"] == "input"]
    for out_at, out in ((i, r) for i, r in enumerate(readings) if r["role"] == "output"):
        for in_at, inp in inputs:
            if ac and levels[in_at] > 0:
                gains.append({"output": out["label"], "input": inp["label"],
                              "gain": round(levels[out_at] / levels[in_at], 3),
                              "basis": "peak", "phase": _phase(correlation[(inp["node"], out["node"])])})
            elif not ac and abs(levels[in_at]) > 1e-9:
                gains.append({"output": out["label"], "input": inp["label"],
                              "gain": round(levels[out_at] / levels[in_at], 3), "basis": "dc"})
    notes = []
    for r in readings:
        if r.get("clipped"):
            notes.append(f"{r['label']} is clipping: the op amp output cannot go beyond about {lo_limit:g} V to {hi_limit:g} V on this supply.")
    return {"analysis": result["analysis"], "readings": readings, "gains": gains,
            "swing": {"low": lo_limit, "high": hi_limit}, "notes": notes, "deck": netlist}
