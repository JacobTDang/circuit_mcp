"""Representative visual starters spanning the official EE 2300 catalog."""
from __future__ import annotations

from .schema import validate_scene

TOPICS = {
    "transfer_function": ("Transfer functions", "H(s) = Vout(s) / Vin(s)", "Relate the circuit model to its input-output ratio."),
    "sinusoidal_steady_state": ("Sinusoidal steady state", "v(t) = |V| cos(ωt + ∠V)", "A rotating phasor projects into a sinusoid."),
    "time_domain": ("Time-domain transient", "x(t) = x(∞) + [x(0)−x(∞)]e^(−t/τ)", "Track stored energy from initial to final state."),
    "linearization": ("Small-signal linearization", "f(x) ≈ f(Q) + f′(Q)(x−Q)", "Zoom into the tangent around the operating point."),
    "feedback_stability": ("Feedback and stability", "T(s) = A(s) / [1 + A(s)β(s)]", "Follow the loop and watch closed-loop poles."),
    "opamp": ("Operational amplifier", "v+ ≈ v−  when negative feedback is active", "Connect feedback, node constraints, and KCL."),
    "nonlinear": ("Nonlinear transfer", "state → equation → piecewise output", "Change device state where the transfer curve bends."),
    "realization": ("Transfer-function realization", "H(s) = H₁(s)H₂(s)", "Build the desired response from circuit stages."),
    "adc": ("A/D conversion", "code = round[(Vin−Vmin)/LSB]", "Sample and map amplitude into discrete codes."),
    "dac": ("D/A conversion", "Vout = Vref · code/(2ⁿ−1)", "Turn a digital code into an analog level."),
    "distortion": ("Distortion and spectra", "SINAD → ENOB", "Connect waveform error to spectral components."),
    "sampling": ("Sampling and aliasing", "falias = |f − kfs|", "Watch spectral replicas fold into Nyquist."),
    "instrumentation": ("Laboratory measurement", "source → circuit → probe → reading", "Place probes and compare expected and measured values."),
}


def template_names() -> list[str]:
    return sorted(TOPICS)


def build_template(name: str, title: str | None = None) -> dict:
    try: heading, equation, caption = TOPICS[name]
    except KeyError as exc: raise ValueError(f"unknown animation template: {name}") from exc
    scene = {
        "title": title or heading, "width": 960, "height": 600,
        "duration_ms": 7000, "seed": 2300,
        "elements": [
            {"id": "heading", "type": "text", "x": 70, "y": 70, "text": heading, "size": 30},
            {"id": "input", "type": "waveform", "x": 80, "y": 150, "w": 260, "h": 120, "color": "blue",
             "points": [[0,60],[35,25],[70,60],[105,95],[140,60],[175,25],[210,60],[245,95]]},
            {"id": "flow", "type": "flow", "x": 350, "y": 210, "w": 150, "color": "red"},
            {"id": "system", "type": "block", "x": 510, "y": 160, "w": 170, "h": 100, "label": heading},
            {"id": "equation", "type": "equation", "x": 150, "y": 390, "text": equation, "size": 27, "color": "blue"},
            {"id": "focus", "type": "highlight", "x": 130, "y": 350, "w": 650, "h": 75, "color": "amber", "opacity": 0},
        ],
        "steps": [
            {"at_ms": 0, "caption": "Start from the physical input and identify the system boundary."},
            {"at_ms": 1800, "caption": caption},
            {"at_ms": 4200, "caption": "Translate the picture into the governing mathematical relationship.",
             "changes": [{"id": "focus", "opacity": .7}]},
            {"at_ms": 6200, "caption": "Check the result against limiting cases and the expected physical behavior."},
        ],
    }
    return validate_scene(scene)
