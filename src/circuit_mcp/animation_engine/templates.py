"""Hand-composed visual starters spanning the official EE 2300 catalog."""
from __future__ import annotations

from .schema import validate_scene

TOPICS = {
    "transfer_function": ("Transfer functions", "H(s) = Vout(s) / Vin(s)", "Follow the signal through a physical RC low-pass."),
    "sinusoidal_steady_state": ("Sinusoidal steady state", "ZC = 1 / jωC", "Move from the circuit to its rotating phasor picture."),
    "time_domain": ("Time-domain transient", "vC(t) = V(1 − e^(−t/RC))", "Watch charge accumulate on the capacitor plates."),
    "linearization": ("Small-signal linearization", "f(x) ≈ f(Q) + f′(Q)(x−Q)", "Replace the nonlinear device near Q with its local model."),
    "feedback_stability": ("Feedback and stability", "T(s) = A(s) / [1 + A(s)β(s)]", "Trace the return path that controls the closed-loop response."),
    "opamp": ("Operational amplifier", "v+ ≈ v−", "Use negative feedback and KCL at the summing node."),
    "nonlinear": ("Nonlinear transfer", "vout = max(vin − VD, 0)", "See exactly when the diode begins conducting."),
    "realization": ("Transfer-function realization", "H(s) = H₁(s)H₂(s)", "Build the response from two real circuit stages."),
    "adc": ("A/D conversion", "code = round[(Vin−Vmin)/LSB]", "Sample the analog node and quantize its voltage."),
    "dac": ("D/A conversion", "Vout = Vref · code/(2ⁿ−1)", "Combine weighted branches into an analog output."),
    "distortion": ("Distortion and spectra", "SINAD → ENOB", "Connect nonlinear clipping to new spectral components."),
    "sampling": ("Sampling and aliasing", "falias = |f − kfs|", "Track samples from the source into the reconstruction path."),
    "instrumentation": ("Laboratory measurement", "source → circuit → probe → reading", "Place the probe across the component, not in series."),
}

def template_names() -> list[str]: return sorted(TOPICS)

def _el(identifier: str, kind: str, x: int, y: int, **values) -> dict:
    return {"id": identifier, "type": kind, "x": x, "y": y, **values}

def _rc_elements() -> list[dict]:
    return [
        _el("source", "voltage_source", 150, 220, w=110, color="blue", angle=90, opacity=0),
        _el("wire_top", "line", 150, 220, w=120, color="ink", opacity=0),
        _el("resistor", "resistor", 270, 220, w=150, color="amber", opacity=0),
        _el("wire_out", "line", 420, 220, w=125, color="ink", opacity=0),
        _el("out_node", "node", 545, 220, color="blue", opacity=0),
        _el("capacitor", "capacitor", 545, 220, w=135, color="green", angle=90, opacity=0),
        _el("return", "line", 545, 355, path="M0 0 L0 45 L-395 45 L-395 -70", color="ink", opacity=0),
        _el("ground", "ground", 545, 400, color="ink", opacity=0),
        _el("charge", "flow", 175, 220, w=355, color="blue", opacity=0),
        _el("vin", "text", 95, 270, text="vin", size=21, color="blue", opacity=0),
        _el("vout", "text", 525, 180, text="vout", size=21, color="blue", opacity=0),
        _el("rlabel", "text", 325, 180, text="R", size=19, color="amber", opacity=0),
        _el("clabel", "text", 585, 305, text="C", size=19, color="green", opacity=0),
    ]

def _opamp_elements() -> list[dict]:
    return [
        _el("vin", "text", 55, 270, text="vin", size=21, color="blue", opacity=0),
        _el("rin", "resistor", 105, 265, w=145, color="amber", opacity=0),
        _el("sum", "node", 280, 265, color="red", opacity=0),
        _el("input_wire", "line", 250, 265, w=90, color="ink", opacity=0),
        _el("amp", "opamp", 340, 295, w=190, h=150, color="ink", opacity=0),
        _el("ground", "ground", 310, 360, color="muted", opacity=0),
        _el("output_wire", "line", 530, 295, w=145, color="ink", opacity=0),
        _el("output", "node", 675, 295, color="blue", opacity=0),
        _el("vout", "text", 700, 302, text="vout", size=21, color="blue", opacity=0),
        _el("feedback", "resistor", 435, 430, w=160, color="green", opacity=0),
        _el("fb_left", "line", 280, 265, path="M0 0 L0 165 L155 165", color="ink", opacity=0),
        _el("fb_right", "line", 595, 430, path="M0 0 L80 0 L80 -135", color="ink", opacity=0),
        _el("loop", "flow", 655, 405, path="M0 0 C-80 95 -295 95 -355 15", color="green", opacity=0),
    ]

def _converter_elements(kind: str) -> list[dict]:
    return [
        _el("signal", "waveform", 55, 210, w=235, h=140, color="blue", points=[[0,70],[28,35],[56,20],[84,35],[112,70],[140,105],[168,120],[196,105],[224,70]], opacity=0),
        _el("lead", "flow", 305, 280, w=100, color="blue", opacity=0),
        _el("converter", "block", 430, 210, w=175, h=140, label=kind.upper(), color="amber", opacity=0),
        _el("bits", "text", 650, 265, text="101101", size=31, color="green", opacity=0),
        _el("steps_plot", "plot", 635, 330, w=220, h=120, color="green", points=[[0,95],[40,95],[40,65],[85,65],[85,30],[130,30],[130,50],[175,50],[175,15],[215,15]], opacity=0),
    ]

def _topic_elements(name: str) -> list[dict]:
    if name in {"opamp", "feedback_stability"}: return _opamp_elements()
    if name in {"adc", "dac"}: return _converter_elements(name)
    if name in {"nonlinear", "linearization", "distortion"}:
        return [
            _el("source", "voltage_source", 60, 270, w=105, color="blue", opacity=0),
            _el("wire", "line", 165, 270, w=95, color="ink", opacity=0),
            _el("device", "diode", 260, 270, w=140, color="amber", opacity=0),
            _el("load", "resistor", 440, 270, w=145, color="green", opacity=0),
            _el("output", "node", 615, 270, color="blue", opacity=0),
            _el("curve", "plot", 650, 195, w=210, h=170, color="red", points=[[0,150],[55,150],[85,145],[105,125],[130,90],[160,50],[195,15]], opacity=0),
            _el("conduction", "flow", 155, 270, w=430, color="red", opacity=0),
        ]
    return _rc_elements()

def build_template(name: str, title: str | None = None) -> dict:
    try: heading, equation, explanation = TOPICS[name]
    except KeyError as exc: raise ValueError(f"unknown animation template: {name}") from exc
    circuit = _topic_elements(name)
    ids = [element["id"] for element in circuit]
    cut1, cut2 = max(1, len(ids) // 3), max(2, len(ids) * 2 // 3)
    first, second, final = ids[:cut1], ids[cut1:cut2], ids[cut2:]
    scene = {
        "title": title or heading, "width": 960, "height": 600, "duration_ms": 9000, "seed": 2300,
        "elements": [
            _el("eyebrow", "text", 60, 52, text="EE 2300  /  VISUAL EXPLANATION", size=14, color="muted"),
            _el("heading", "text", 60, 100, text=heading, size=34, color="ink"),
            _el("rule", "line", 60, 125, w=840, color="muted"), *circuit,
            _el("equation_card", "highlight", 95, 475, w=770, h=78, color="blue", opacity=0),
            _el("equation", "equation", 135, 526, text=equation, size=27, color="blue", opacity=0),
        ],
        "steps": [
            {"at_ms": 0, "caption": "Begin with the source and identify where energy enters the circuit.", "changes": [{"id": item, "opacity": 1} for item in first]},
            {"at_ms": 1800, "caption": "Build the signal path one physical component at a time.", "changes": [{"id": item, "opacity": 1} for item in second]},
            {"at_ms": 3800, "caption": explanation, "changes": [{"id": item, "opacity": 1} for item in final]},
            {"at_ms": 5900, "caption": "Follow the highlighted direction and connect cause to effect.", "changes": [{"id": item, "opacity": 1, "scale": 1.04} for item in ids if item in {"charge", "loop", "conduction", "lead"}]},
            {"at_ms": 7300, "caption": "The equation is a compact description of the circuit you just watched.", "changes": [{"id": "equation_card", "opacity": .75}, {"id": "equation", "opacity": 1}]},
        ],
    }
    return validate_scene(scene)
