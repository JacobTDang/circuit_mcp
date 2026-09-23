"""Netlist -> symbolic transfer function, via lcapy.

lcapy refuses an ``s``-dependent component value ("Constant expression ...
cannot depend on s"), on both the ``opamp`` variant and a plain VCVS. Finite
op-amp gain-bandwidth is therefore modelled by solving with a constant gain
symbol and substituting ``A -> A(s)`` afterwards. That is equivalent: the MNA
solve treats the gain as an opaque symbol either way, and there is no
differentiation or integration for the s-dependence to interact with.
"""
from __future__ import annotations

import sympy as sp
from lcapy import Circuit, s as _lcapy_s

# lcapy reports a port nothing reaches by failing to invert its MNA matrix.
_DISJOINT_ERRORS = (ValueError, RuntimeError, AttributeError, TypeError)

from .symbols import SubstitutionError, bind, safe_subs

# lcapy's own Laplace variable, not a hand-made one. It carries complex/finite,
# which do not restrict sign and so do not break pole solving -- verified. Using
# a bare Symbol("s") instead would be a *different object* from the s inside
# every lcapy result, making identical expressions compare unequal on any
# circuit containing a capacitor. See SymbolConflictError in symbols.py.
S = _lcapy_s.sympy

# Sign-restricting assumptions that would break pole solving.
_SIGN_ASSUMPTIONS = ("positive", "negative", "nonnegative", "nonpositive")


class AssumptionError(ValueError):
    """A symbol carries assumptions that would silently distort the result."""


class PortError(ValueError):
    """A port that cannot be driven or measured as asked."""


def transfer(netlist: str, in_pos, in_neg, out_pos, out_neg, kind: str = "voltage") -> sp.Expr:
    """Symbolic transfer function between two node pairs.

    ``kind`` says what drives the input pair. ``voltage`` applies
    ``V(in_pos) - V(in_neg)`` and returns a dimensionless gain. ``current``
    drives a current *into* ``in_pos`` and *out of* ``in_neg`` and returns a
    transresistance in ohms -- which is what a photodiode, a DAC's summing
    node, or any other current source needs. Writing such a circuit as a
    voltage source behind a series resistor and mapping the source current by
    hand is a sign error waiting to happen, which is the whole reason this
    takes a kind.
    """
    if kind not in ("voltage", "current"):
        raise ValueError(
            f"Unknown input kind {kind!r}. Use 'voltage' for a voltage between "
            f"the input nodes, or 'current' for a current into in_pos."
        )
    circuit = Circuit(netlist)
    if kind == "voltage":
        return circuit.transfer(in_pos, in_neg, out_pos, out_neg).sympy
    return circuit.transimpedance(in_pos, in_neg, out_pos, out_neg).sympy


def _without_sources_across(netlist: str, pos, neg) -> str:
    """The netlist with any independent source wired straight across the port dropped.

    Impedance is measured with the independent sources killed, and a killed
    voltage source is a short. Left in place, a source across the port would
    make every answer zero -- and a source across the port is exactly how a
    student draws the input they are measuring.
    """
    kept = []
    port = {str(pos), str(neg)}
    for line in netlist.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0][:1] in ("V", "I") and {fields[1], fields[2]} == port:
            continue
        kept.append(line)
    return "\n".join(kept)


def port_impedance(netlist: str, pos, neg) -> sp.Expr:
    """Impedance looking into a port, with the independent sources killed.

    Section 1.5 of the course text treats input resistance as a core quantity,
    and nothing symbolic here could produce one: it was being measured
    numerically in ngspice instead.

    A port shorted by a wire answers zero, and a port the rest of the circuit
    does not reach has no answer at all. Both are refused rather than returned,
    because both are a netlist that does not say what its author meant. A port
    whose impedance merely *tends* to zero as the open-loop gain grows -- the
    virtual ground of an inverting amplifier -- is a real answer and is kept.
    """
    try:
        impedance = Circuit(_without_sources_across(netlist, pos, neg)).impedance(pos, neg).sympy
    except _DISJOINT_ERRORS as exc:
        raise PortError(
            f"Nothing connects the port ({pos}, {neg}) to the rest of the circuit, "
            f"so there is no impedance to report: {exc}"
        ) from exc
    if impedance == 0:
        raise PortError(
            f"The port ({pos}, {neg}) is a short circuit, so its impedance is zero "
            f"by construction. Measure across the component you meant, or remove "
            f"the wire joining these nodes."
        )
    if impedance.has(sp.oo, sp.zoo, sp.nan):
        raise PortError(
            f"The port ({pos}, {neg}) is open, so no current can be driven into it."
        )
    return impedance


def _gain_symbol(expr: sp.Expr, name: str) -> sp.Symbol:
    """The real symbol if present, else a bare one so safe_subs reports it."""
    return bind(expr).get(name, sp.Symbol(name))


def ideal_limit(expr: sp.Expr, gain: str = "A") -> sp.Expr:
    """Collapse to the ideal op-amp result by taking the open-loop gain to infinity.

    Raises if the gain symbol is absent: ``sp.limit`` against a symbol that does
    not occur returns the expression unchanged and raises nothing, so a circuit
    with no gain would come back labelled "ideal" while being untouched.
    """
    available = bind(expr)
    if gain not in available:
        raise SubstitutionError(
            f"No symbol named {gain!r} in the expression, so taking it to "
            f"infinity would return the input unchanged. Present: "
            f"{sorted(available)}."
        )
    return sp.limit(expr, available[gain], sp.oo)


def with_finite_gbw(expr: sp.Expr, gain: str = "A", a0: str = "A0", wp: str = "wp") -> sp.Expr:
    """Substitute a single-pole open-loop gain ``A(s) = A0 / (1 + s/wp)``."""
    A0, Wp = sp.symbols(f"{a0} {wp}", positive=True)
    substituted = safe_subs(expr, _gain_symbol(expr, gain), A0 / (1 + S / Wp))
    return sp.cancel(sp.together(substituted))


def poles(expr: sp.Expr, var: sp.Symbol = S) -> list[sp.Expr]:
    """Poles of ``expr``, refusing to run against a sign-restricted variable."""
    expr = sp.sympify(expr)
    for sym in expr.free_symbols:
        if str(sym) != str(var):
            continue
        bad = [a for a in _SIGN_ASSUMPTIONS if sym.assumptions0.get(a)]
        if bad:
            raise AssumptionError(
                f"{sym!r} carries the assumption(s) {bad}. Poles are negative, so "
                f"solve() would return an empty list instead of erroring. Declare "
                f"the Laplace variable with no assumptions."
            )
        var = sym  # use the expression's own object

    denominator = sp.denom(sp.cancel(sp.together(expr)))
    if not denominator.has(var):
        return []
    return sp.solve(denominator, var)
