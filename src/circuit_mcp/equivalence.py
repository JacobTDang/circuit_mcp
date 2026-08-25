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


def equivalent(a: sp.Expr, b: sp.Expr) -> EquivalenceResult:
    """Decide whether two expressions are algebraically equal."""
    a, b = sp.sympify(a), sp.sympify(b)

    # A name bound to differing assumptions on each side would make identical
    # expressions compare unequal. Refuse rather than return a wrong verdict.
    assert_no_conflicts(a, b)

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
