"""Module 2 HW1 as the solutions sheet sees it: eight problems, in page order.

Real values from the assignment, so the sheet is exercised on the thing it was
built for rather than on a shape invented to make it pass.
"""
from __future__ import annotations

TAG = "m2-hw1"

PROBLEMS: list[dict] = [
    {"title": "2.9 (a) gain and input resistance", "topic": "op-amps", "page": 1,
     "prompt": "Find vo/vI and Rin for the inverting amplifier of Fig. P2.9(a).",
     "solution": {
         "given": [{"name": "R1", "value": 15000, "unit": "Ω"}, {"name": "Rf", "value": 90000, "unit": "Ω"}],
         "steps": [{"expression": "-Rf/R1", "note": "inverting gain"},
                   {"expression": "-6", "note": "with the given values"}],
         "answer": {"expression": "-6", "unit": "V/V"},
         "schematic": "R1 1 2 15e3\nRf 2 3 90e3\nE1 3 0 opamp 0 2 {A}"}},
    {"title": "2.9 (b) gain with a loaded output", "topic": "op-amps", "page": 2,
     "prompt": "Repeat for Fig. P2.9(b), where the output drives a 15 kΩ load.",
     "solution": {
         "given": [{"name": "R1", "value": 15000, "unit": "Ω"}, {"name": "Rf", "value": 90000, "unit": "Ω"}],
         "steps": [{"expression": "-Rf/R1", "note": "the load draws from the output, not the summing node"},
                   {"expression": "-6", "note": "with the given values"}],
         "answer": {"expression": "-6", "unit": "V/V"}}},
    {"title": "2.9 (c) gain with a shunt at the summing node", "topic": "op-amps", "page": 3,
     "prompt": "Repeat for Fig. P2.9(c), with 15 kΩ from the summing node to ground.",
     "solution": {
         "given": [{"name": "R1", "value": 15000, "unit": "Ω"}, {"name": "Rf", "value": 90000, "unit": "Ω"}],
         "steps": [{"expression": "-Rf/R1", "note": "the shunt sits on a virtual ground and carries nothing"},
                   {"expression": "-6", "note": "with the given values"}],
         "answer": {"expression": "-6", "unit": "V/V"}}},
    {"title": "2.9 (d) gain with the + input through a resistor", "topic": "op-amps", "page": 4,
     "prompt": "Repeat for Fig. P2.9(d), with the + input grounded through 15 kΩ.",
     "solution": {
         "given": [{"name": "R1", "value": 15000, "unit": "Ω"}, {"name": "Rf", "value": 90000, "unit": "Ω"}],
         "steps": [{"expression": "-Rf/R1", "note": "no current flows in the + resistor, so it drops nothing"},
                   {"expression": "-6", "note": "with the given values"}],
         "answer": {"expression": "-6", "unit": "V/V"}}},
    {"title": "2.34 inverting amplifier design", "topic": "op-amps", "page": 5,
     "prompt": "Design an inverting amplifier of gain -10 with an input resistance of 100 kΩ.",
     "solution": {
         "given": [{"name": "Rin", "value": 100000, "unit": "Ω"}],
         "steps": [{"expression": "10*Rin", "note": "R1 is the input resistance, and a gain of -10 needs Rf = 10*R1"},
                   {"expression": "1000000", "note": "with the given value"}],
         "answer": {"expression": "1000000", "unit": "Ω"}}},
    {"title": "2.48 (a) summing amplifier", "topic": "op-amps", "page": 6,
     "prompt": "Show that the circuit of Fig. P2.48 sums its inverting and noninverting inputs.",
     "solution": {
         "given": [{"name": "Rf", "value": 40000, "unit": "Ω"}, {"name": "RN1", "value": 10000, "unit": "Ω"}],
         "steps": [{"expression": "-Rf/RN1", "note": "the inverting path"},
                   {"expression": "-4", "note": "with the given values"}],
         "answer": {"expression": "-4", "unit": "V/V"}}},
    {"title": "2.48 (b) summing amplifier design", "topic": "op-amps", "page": 7,
     "prompt": "Design a circuit for vo = -4*vN1 + vP1 + 3*vP2 with no resistor below 10 kΩ.",
     "solution": {
         "given": [{"name": "RN1", "value": 10000, "unit": "Ω"}],
         "steps": [{"expression": "4*RN1", "note": "the feedback resistor sets the inverting gain of 4"},
                   {"expression": "40000", "note": "with the given value"}],
         "answer": {"expression": "40000", "unit": "Ω"},
         "schematic": ("VN1 1 0 {vN1}\nVP1 4 0 {vP1}\nVP2 5 0 {vP2}\n"
                       "RN1 1 2 10e3\nRf 2 3 40e3\n"
                       "RP1 4 6 30e3\nRP2 5 6 10e3\nRP0 6 0 30e3\n"
                       "E1 3 0 opamp 6 2 {A}")}},
    {"title": "2.62 difference amplifier", "topic": "op-amps", "page": 8,
     "prompt": "Find the differential gain of the difference amplifier of Fig. P2.62.",
     "solution": {
         "given": [{"name": "R1", "value": 10000, "unit": "Ω"}, {"name": "R2", "value": 100000, "unit": "Ω"}],
         "steps": [{"expression": "R2/R1", "note": "matched arms, so the differential gain is the ratio"},
                   {"expression": "10", "note": "with the given values"}],
         "answer": {"expression": "10", "unit": "V/V"}}},
]
