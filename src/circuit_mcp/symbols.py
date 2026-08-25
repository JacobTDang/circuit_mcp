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


class SymbolConflictError(ValueError):
    """One name is bound to two symbols whose assumptions differ.

    SymPy hashes a symbol on its name *and* its assumptions, so ``Symbol('A')``
    and ``Symbol('A', positive=True)`` are distinct objects. Expressions built
    from each print identically yet never compare equal, and a numeric oracle
    assigns them independent values. The result is a confident wrong verdict on
    correct work -- the one outcome this tool must never produce.
    """


def conflicts(*exprs: sp.Expr) -> dict[str, list[sp.Symbol]]:
    """Names bound to more than one distinct symbol across ``exprs``."""
    seen: dict[str, set[sp.Symbol]] = {}
    for expr in exprs:
        for sym in sp.sympify(expr).free_symbols:
            seen.setdefault(str(sym), set()).add(sym)
    return {
        name: sorted(syms, key=sp.srepr)
        for name, syms in seen.items()
        if len(syms) > 1
    }


def assert_no_conflicts(*exprs: sp.Expr) -> None:
    """Raise if any name is bound to symbols with differing assumptions."""
    found = conflicts(*exprs)
    if not found:
        return
    detail = "; ".join(
        f"{name!r} appears as " + " and ".join(sp.srepr(s) for s in syms)
        for name, syms in found.items()
    )
    raise SymbolConflictError(
        f"Same name, different assumptions: {detail}. These print identically "
        f"but are distinct to SymPy, so comparing them would report correct "
        f"work as wrong. Reconcile them first with reconcile(expr, reference), "
        f"or parse with the reference symbols supplied."
    )


def reconcile(expr: sp.Expr, reference: sp.Expr | dict[str, sp.Symbol]) -> sp.Expr:
    """Rewrite ``expr``'s symbols onto the same objects ``reference`` uses.

    The primary defence is to parse with the reference symbols supplied in the
    first place; this repairs an expression that was already built without them.
    """
    expr = sp.sympify(expr)
    ref = reference if isinstance(reference, dict) else bind(sp.sympify(reference))
    mapping = {
        sym: ref[str(sym)]
        for sym in expr.free_symbols
        if str(sym) in ref and ref[str(sym)] is not sym
    }
    return expr.xreplace(mapping) if mapping else expr
