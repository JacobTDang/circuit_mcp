"""Two-oracle equivalence checking.

``simplify`` is slow and sometimes indecisive on the rational functions that
second-order filters produce, so a symbolic result alone is not enough. Random
numeric substitution is the second oracle: it settles undecided cases and, when
two expressions genuinely differ, produces a concrete counterexample -- which is
what makes the feedback useful rather than just "wrong".
"""
from __future__ import annotations

from dataclasses import dataclass

import sympy as sp

from .parsing import expand_sums
from .symbols import assert_no_conflicts

# Fixed, varied rationals so results are reproducible across runs. Values are
# deliberately unrelated to avoid accidental cancellation, and positive so they
# never violate a symbol's assumptions.
_TRIALS: tuple[tuple[int, int], ...] = (
    (3, 7), (11, 5), (2, 13), (17, 3), (23, 19), (5, 29), (31, 7), (13, 11),
)


@dataclass(frozen=True)
class EquivalenceResult:
    equivalent: bool
    oracle: str  # "symbolic" (a proof) or "numeric" (probabilistic)
    counterexample: dict[sp.Symbol, sp.Rational] | None = None
    detail: str = ""


def _trial_values(symbols: list[sp.Symbol], trial: int) -> dict:
    """Distinct positive rationals, rotated per trial."""
    num, den = _TRIALS[trial % len(_TRIALS)]
    return {
        sym: sp.Rational(num + 2 * i, den + i)
        for i, sym in enumerate(symbols)
    }


# How many terms a symbolic-bound sum is checked at. Fixed, like _TRIALS, so a
# verdict is reproducible.
_SUM_TERMS: tuple[int, ...] = (1, 2, 3, 4)


def _symbolic_bounds(*expressions: sp.Expr) -> set[sp.Symbol]:
    """Names used as a summation limit rather than as a quantity."""
    bounds: set[sp.Symbol] = set()
    for expression in expressions:
        for summation in expression.atoms(sp.Sum):
            for _, lower, upper in summation.limits:
                bounds |= {symbol for symbol in (lower, upper) if isinstance(symbol, sp.Symbol)}
    return bounds


def _expanded_at(expressions: tuple[sp.Expr, sp.Expr], bound: sp.Symbol, terms: int) -> tuple[sp.Expr, sp.Expr]:
    """Both sides with the bound set to a concrete term count, sums written out.

    Written out rather than ``doit()``-ed: a per-resistor sum names ``RN_i``,
    and until that is renamed per term every term is the same one.
    """
    return tuple(expand_sums(expression, bound, terms).doit() for expression in expressions)  # type: ignore[return-value]


def equivalent(a: sp.Expr, b: sp.Expr) -> EquivalenceResult:
    """Decide whether two expressions are algebraically equal."""
    a, b = sp.sympify(a), sp.sympify(b)

    # A name bound to differing assumptions on each side would make identical
    # expressions compare unequal. Refuse rather than return a wrong verdict.
    assert_no_conflicts(a, b)

    # A summation bound is a term count, not a continuous quantity, and the
    # numeric oracle hangs when it hands one a random rational. Check the two
    # sides at each of the first few term counts instead.
    bounds = _symbolic_bounds(a, b)
    if bounds:
        if len(bounds) > 1:
            return EquivalenceResult(
                False, "symbolic", None,
                f"more than one symbolic summation bound ({', '.join(sorted(str(x) for x in bounds))}); "
                f"this check expands one bound at a time",
            )
        bound = bounds.pop()
        for terms in _SUM_TERMS:
            left, right = _expanded_at((a, b), bound, terms)
            verdict = equivalent(left, right)
            if not verdict.equivalent:
                return EquivalenceResult(
                    False, verdict.oracle, verdict.counterexample,
                    f"differ at {bound} = {terms}: {verdict.detail}",
                )
        return EquivalenceResult(
            True, "numeric", None,
            f"agreed at {bound} = " + ", ".join(str(n) for n in _SUM_TERMS),
        )

    # Oracle 1: symbolic. cancel/together handle rational functions far better
    # than simplify alone, which is the shape this tool mostly sees.
    delta = sp.simplify(sp.cancel(sp.together(a - b)))
    if delta == 0:
        return EquivalenceResult(True, "symbolic", None, "proved by simplification")

    # Oracle 2: numeric. Also the source of the counterexample.
    symbols = sorted(a.free_symbols | b.free_symbols, key=str)
    if not symbols:
        return EquivalenceResult(False, "symbolic", None, f"constants differ by {delta}")

    evaluated = 0
    for trial in range(len(_TRIALS)):
        values = _trial_values(symbols, trial)
        try:
            diff = sp.nsimplify(sp.expand(a.subs(values) - b.subs(values)))
        except (ZeroDivisionError, TypeError, ValueError):
            continue
        if diff.has(sp.zoo, sp.nan, sp.oo):  # hit a pole; try other values
            continue
        evaluated += 1
        if sp.simplify(diff) != 0:
            return EquivalenceResult(
                False, "numeric", values,
                f"differ by {diff} at the counterexample",
            )

    if evaluated == 0:
        return EquivalenceResult(
            False, "numeric", None,
            "every trial hit a pole or failed to evaluate; inconclusive",
        )

    # Symbolic could not prove it, but every numeric trial agreed.
    return EquivalenceResult(
        True, "numeric", None,
        f"agreed on {evaluated} random trials; simplify left {delta}",
    )
