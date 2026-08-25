"""Symbol binding for expressions produced by lcapy.

lcapy builds symbols carrying assumptions (``positive=True`` and friends), so
``sympy.Symbol('A')`` is a *different object* and will not match. ``subs()``
against the wrong one returns the expression unchanged and raises nothing.

A substitution that quietly does nothing yields a plausible wrong ground truth,
which is the one failure this tool cannot afford. So substitution here is loud.
"""
from __future__ import annotations

import sympy as sp


class SubstitutionError(RuntimeError):
    """A substitution had no effect, so the caller's intent was not applied."""


def bind(expr: sp.Expr) -> dict[str, sp.Symbol]:
    """Map symbol *name* -> the actual symbol object inside ``expr``.

    Use this to obtain a symbol that will genuinely match during ``subs``,
    rather than constructing one and hoping the assumptions line up.
    """
    return {str(sym): sym for sym in expr.free_symbols}


def safe_subs(expr: sp.Expr, old: sp.Expr, new: sp.Expr) -> sp.Expr:
    """``expr.subs(old, new)``, but raise if nothing actually changed."""
    result = expr.subs(old, new)
    if result == expr:
        available = bind(expr)
        name = str(old)
        if name in available:
            detail = (
                f"A symbol named {name!r} exists in the expression but is a "
                f"different object -- assumptions differ "
                f"({available[name].assumptions0!r}). Bind it with bind(expr)"
                f"[{name!r}] instead of constructing a new Symbol."
            )
        else:
            detail = (
                f"No symbol named {name!r} in the expression. "
                f"Present: {sorted(available)}"
            )
        raise SubstitutionError(
            f"Substituting {old} did not change the expression. {detail}"
        )
    return result
