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

from .symbols import bind, safe_subs

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


def transfer(netlist: str, in_pos, in_neg, out_pos, out_neg) -> sp.Expr:
    """Symbolic transfer function between two node pairs."""
    return Circuit(netlist).transfer(in_pos, in_neg, out_pos, out_neg).sympy


def _gain_symbol(expr: sp.Expr, name: str) -> sp.Symbol:
    """The real symbol if present, else a bare one so safe_subs reports it."""
    return bind(expr).get(name, sp.Symbol(name))


def ideal_limit(expr: sp.Expr, gain: str = "A") -> sp.Expr:
    """Collapse to the ideal op-amp result by taking the open-loop gain to infinity."""
    return sp.limit(expr, _gain_symbol(expr, gain), sp.oo)


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
