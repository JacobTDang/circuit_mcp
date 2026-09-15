"""The eight Lab 1 circuits as build descriptions. Real coursework, not toys."""

EXP1_NONINVERTING = {
    "supply": {"vplus": 15, "vminus": -15},
    "chips": [{"ref": "U1", "part": "LM324"}],
    "parts": [
        {"ref": "R3", "kind": "pot", "value": "10k", "nodes": ["vsine", "vi", "gnd"]},
        {"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["fb", "gnd"]},
        {"ref": "R2", "kind": "resistor", "value": "15k", "nodes": ["out", "fb"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "vi", "inn": "fb", "out": "out"}],
    "sources": [{"ref": "VS", "kind": "sine", "node": "vsine", "vrms": 1.0, "freq": 1000}],
    "probes": [{"label": "CH1 vi", "node": "vi", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
    "pot_positions": {"R3": 0.5},
}

EXP3_INVERTING = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "2.2k", "nodes": ["vi", "inn"]},
        {"ref": "R2", "kind": "pot", "value": "100k", "nodes": ["inn", "out", "out"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "gnd", "inn": "inn", "out": "out"}],
    "sources": [{"ref": "VS", "kind": "sine", "node": "vi", "vrms": 0.2, "freq": 1000}],
    "probes": [{"label": "CH1 vi", "node": "vi", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
    "pot_positions": {"R2": 0.5},
}

EXP4A_DIVIDER_LED = {
    "supply": {"vplus": 15, "vminus": 0},
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "2.2k", "nodes": ["vdc", "va"]},
        {"ref": "R2", "kind": "resistor", "value": "6.8k", "nodes": ["va", "gnd"]},
        {"ref": "RL", "kind": "resistor", "value": "1k", "nodes": ["va", "vd"]},
        {"ref": "D1", "kind": "led", "nodes": ["vd", "gnd"]},
    ],
    "sources": [{"ref": "VDC", "kind": "dc", "node": "vdc", "volts": 15}],
    "probes": [{"label": "VA", "node": "va", "role": "output"}],
}

EXP4B_BUFFERED = {
    "supply": {"vplus": 15, "vminus": 0},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "2.2k", "nodes": ["vdc", "va"]},
        {"ref": "R2", "kind": "resistor", "value": "6.8k", "nodes": ["va", "gnd"]},
        {"ref": "RL", "kind": "resistor", "value": "1k", "nodes": ["vb", "vd"]},
        {"ref": "D1", "kind": "led", "nodes": ["vd", "gnd"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "va", "inn": "vb", "out": "vb"}],
    "sources": [{"ref": "VDC", "kind": "dc", "node": "vdc", "volts": 15}],
    "probes": [{"label": "VA", "node": "va", "role": "input"}, {"label": "VB", "node": "vb", "role": "output"}],
}

EXP5_INTEGRATOR = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "10k", "nodes": ["vi", "inn"]},
        {"ref": "R2", "kind": "resistor", "value": "470k", "nodes": ["inn", "out"]},
        {"ref": "C1", "kind": "capacitor", "value": "0.1u", "nodes": ["inn", "out"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "gnd", "inn": "inn", "out": "out"}],
    "sources": [{"ref": "VS", "kind": "square", "node": "vi", "vpp": 10, "freq": 500}],
    "probes": [{"label": "CH1 vi", "node": "vi", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
}

EXP6_DIFFERENCE = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R1", "kind": "resistor", "value": "1k", "nodes": ["vb", "inn"]},
        {"ref": "R2", "kind": "resistor", "value": "10k", "nodes": ["inn", "out"]},
        {"ref": "R3", "kind": "resistor", "value": "2.2k", "nodes": ["va", "inp"]},
        {"ref": "R4", "kind": "resistor", "value": "22k", "nodes": ["inp", "gnd"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "inp", "inn": "inn", "out": "out"}],
    "sources": [{"ref": "VA", "kind": "sine", "node": "va", "vrms": 0.25, "freq": 1000},
                {"ref": "VB", "kind": "dc", "node": "vb", "volts": 0}],
    "probes": [{"label": "CH1 va", "node": "va", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
}

EXP7_DAC = {
    "supply": {"vplus": 10, "vminus": -10},
    "chips": [{"ref": "U1", "part": "LM324"}],
    "parts": [
        {"ref": "RD2", "kind": "resistor", "value": "2.5k", "nodes": ["d2", "sum"]},
        {"ref": "RD1", "kind": "resistor", "value": "5k", "nodes": ["d1", "sum"]},
        {"ref": "RD0", "kind": "resistor", "value": "10k", "nodes": ["d0", "sum"]},
        {"ref": "RF", "kind": "resistor", "value": "10k", "nodes": ["sum", "out"]},
    ],
    "opamps": [{"ref": "U1A", "chip": "U1", "section": "A", "inp": "gnd", "inn": "sum", "out": "out"}],
    "sources": [{"ref": "VD2", "kind": "dc", "node": "d2", "volts": 1},
                {"ref": "VD1", "kind": "dc", "node": "d1", "volts": 0},
                {"ref": "VD0", "kind": "dc", "node": "d0", "volts": 1}],
    "probes": [{"label": "vo", "node": "out", "role": "output"}],
}

EXP8_INSTRUMENTATION = {
    "supply": {"vplus": 8, "vminus": -8},
    "chips": [{"ref": "U1", "part": "LMC660"}],
    "parts": [
        {"ref": "R2a", "kind": "resistor", "value": "10k", "nodes": ["o2", "n2"]},
        {"ref": "R1", "kind": "resistor", "value": "10k", "nodes": ["n2", "n1"]},
        {"ref": "R2b", "kind": "resistor", "value": "10k", "nodes": ["n1", "o1"]},
        {"ref": "R3a", "kind": "resistor", "value": "1k", "nodes": ["o2", "dn"]},
        {"ref": "R4a", "kind": "resistor", "value": "10k", "nodes": ["dn", "out"]},
        {"ref": "R3b", "kind": "resistor", "value": "1k", "nodes": ["o1", "dp"]},
        {"ref": "R4b", "kind": "resistor", "value": "10k", "nodes": ["dp", "gnd"]},
    ],
    "opamps": [
        {"ref": "U1A", "chip": "U1", "section": "A", "inp": "v2", "inn": "n2", "out": "o2"},
        {"ref": "U1B", "chip": "U1", "section": "B", "inp": "v1", "inn": "n1", "out": "o1"},
        {"ref": "U1C", "chip": "U1", "section": "C", "inp": "dp", "inn": "dn", "out": "out"},
    ],
    "sources": [{"ref": "V1", "kind": "sine", "node": "v1", "vrms": 0.15, "freq": 1000},
                {"ref": "V2", "kind": "dc", "node": "v2", "volts": 0}],
    "probes": [{"label": "CH1 v1", "node": "v1", "role": "input"}, {"label": "CH2 vo", "node": "out", "role": "output"}],
}

ALL = {"exp1": EXP1_NONINVERTING, "exp3": EXP3_INVERTING, "exp4a": EXP4A_DIVIDER_LED, "exp4b": EXP4B_BUFFERED,
       "exp5": EXP5_INTEGRATOR, "exp6": EXP6_DIFFERENCE, "exp7": EXP7_DAC, "exp8": EXP8_INSTRUMENTATION}
