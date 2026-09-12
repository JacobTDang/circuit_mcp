"""Physical parts shared by the breadboard layout and the bench simulator.

The pin map is the one thing here that must never be wrong: a pin number off
by one sends a student wiring into the wrong leg of the chip. It is data, not
inference, and the tests check it against the datasheet numbers.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


class PartError(ValueError):
    """A part, pin, or value the library does not know."""


@dataclass(frozen=True)
class OpAmpSection:
    name: str
    out: int
    inn: int
    inp: int


@dataclass(frozen=True)
class Chip:
    part: str
    pins: int
    vplus: int
    vminus: int
    sections: tuple[OpAmpSection, ...]
    headroom_high: float  # volts the output stops short of V+
    headroom_low: float   # volts the output stops short of V-

    def section(self, name: str) -> OpAmpSection:
        for section in self.sections:
            if section.name == name.upper():
                return section
        names = ", ".join(s.name for s in self.sections)
        raise PartError(f"{self.part} has no section {name!r}; sections are {names}")


# Industry-standard quad op amp DIP-14: V+ pin 4, V- pin 11.
QUAD_DIP14 = (
    OpAmpSection("A", out=1, inn=2, inp=3),
    OpAmpSection("B", out=7, inn=6, inp=5),
    OpAmpSection("C", out=8, inn=9, inp=10),
    OpAmpSection("D", out=14, inn=13, inp=12),
)

CHIPS: dict[str, Chip] = {
    # LM324: output reaches V+ - 1.5 V; low side is within tens of mV of V- when
    # sinking little current, 0.5 V is a conservative figure for a loaded output.
    "LM324": Chip("LM324", 14, 4, 11, QUAD_DIP14, headroom_high=1.5, headroom_low=0.5),
    # LMC660: rail-to-rail output.
    "LMC660": Chip("LMC660", 14, 4, 11, QUAD_DIP14, headroom_high=0.1, headroom_low=0.1),
}


def chip(part: str) -> Chip:
    try:
        return CHIPS[part.upper()]
    except KeyError as exc:
        raise PartError(f"unknown chip {part!r}; known: {', '.join(sorted(CHIPS))}") from exc


TWO_TERMINAL = {"resistor": "R", "capacitor": "C", "inductor": "L"}
UNITS = {"resistor": "Ω", "capacitor": "F", "inductor": "H"}

_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "meg": 1e6, "M": 1e6, "g": 1e9}
_VALUE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(meg|[pnuµmkMg])?\s*[FHΩ]?\s*$")


def parse_value(text: str) -> float:
    """'15k' -> 15000.0, '0.1u' -> 1e-7, '470k' -> 470000.0. Loud on anything else."""
    match = _VALUE.match(str(text))
    if not match:
        raise PartError(f"cannot read component value {text!r}")
    number, prefix = match.groups()
    return float(number) * (_SI[prefix] if prefix else 1.0)


THERMAL_VOLTAGE = 0.02585


def led_saturation_current(forward_volts: float = 2.0, at_amps: float = 3e-3, emission: float = 2.0) -> float:
    """Diode IS that puts ``forward_volts`` across the LED at ``at_amps``."""
    return at_amps / math.exp(forward_volts / (emission * THERMAL_VOLTAGE))


def opamp_subcircuit() -> str:
    """A high-gain op amp whose output stops ``hi`` below V+ and ``lo`` above V-.

    Clipping is the one nonideality a first lab actually sees, so it is the one
    the model keeps. Everything else is ideal.
    """
    return (
        ".subckt railamp inp inn out vp vn params: hi=1.5 lo=0.5\n"
        "E1 oa 0 inp inn 100k\n"
        "Rout oa out 50\n"
        "Vhi vp clamp_hi dc {hi}\n"
        "Dhi out clamp_hi dclamp\n"
        "Vlo clamp_lo vn dc {lo}\n"
        "Dlo clamp_lo out dclamp\n"
        ".model dclamp D(N=0.01)\n"
        ".ends railamp"
    )
