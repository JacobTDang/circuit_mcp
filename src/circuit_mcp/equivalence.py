"""Two-oracle equivalence checking.

``simplify`` is slow and sometimes indecisive on the rational functions that
second-order filters produce, so a symbolic result alone is not enough. Random
numeric substitution is the second oracle: it settles undecided cases and, when
two expressions genuinely differ, produces a concrete counterexample -- which is
what makes the feedback useful rather than just "wrong".

The trials are positive, because lcapy's component symbols are and a value
outside a symbol's assumptions is no evidence. But at a positive value
``Abs(x)``, ``re(x)`` and ``conjugate(x)`` are ``x``, so a step that drops a
magnitude, a real part or a sign agrees at every positive trial. Where one of
those functions appears, the symbols inside it are tried again at negative and
then complex values -- only those symbols, so a square-root or log split of
positive component values elsewhere in the expression still holds.
"""
from __future__ import annotations

from dataclasses import dataclass

import sympy as sp

from .parsing import expand_sums, printed
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
    counterexample: dict[sp.Symbol, sp.Expr] | None = None  # a value may be complex
    detail: str = ""


def _trial_values(symbols: list[sp.Symbol], trial: int) -> dict:
    """Distinct positive rationals, rotated per trial."""
    num, den = _TRIALS[trial % len(_TRIALS)]
    return {
        sym: sp.Rational(num + 2 * i, den + i)
        for i, sym in enumerate(symbols)
    }


# Functions that cannot be told from their argument, or from a constant, at a
# positive value: there Abs(x) = re(x) = conjugate(x) = Max(x, 0) = x,
# im(x) = arg(x) = 0, and sign(x) = Heaviside(x) = 1.
_SIGN_BLIND = (sp.Abs, sp.sign, sp.arg, sp.re, sp.im, sp.conjugate, sp.Max, sp.Min, sp.Heaviside)

# re(Z) = Z at every real Z as well, so for these only a complex value is a test.
_PHASE_BLIND = (sp.Abs, sp.arg, sp.re, sp.im, sp.conjugate)

# Defined on the real line only, so what they take is a real quantity.
_REAL_ONLY = (sp.Max, sp.Min, sp.Heaviside)


def _hidden_from_positive_trials(*expressions: sp.Expr) -> tuple[set, set]:
    """The symbols positive trials cannot test: those to try negative, and those to try complex.

    A symbol assumed nonnegative is never in either, and one assumed real never
    in the second. A function whose argument is written with ``j`` takes its
    other symbols as real: ``Abs(R + 1/(j*w*C))`` is a phasor built from real
    parts, and its magnitude is ``sqrt(R^2 + 1/(w*C)^2)`` only for real R, w, C.
    """
    negative: set[sp.Symbol] = set()
    complex_: set[sp.Symbol] = set()
    real: set[sp.Symbol] = set()
    for expression in expressions:
        for function in expression.atoms(*_SIGN_BLIND):
            negative |= function.free_symbols
            if isinstance(function, _REAL_ONLY):
                real |= function.free_symbols
            elif isinstance(function, _PHASE_BLIND) and not function.has(sp.I):
                complex_ |= function.free_symbols
        # sqrt(x^2) is Abs(x) in disguise, and SymPy says so only once x is
        # known to be real.
        as_real = {symbol: sp.Dummy(symbol.name, real=True)
                   for symbol in expression.free_symbols if not symbol.is_extended_real}
        back = {dummy: symbol for symbol, dummy in as_real.items()}
        for function in expression.xreplace(as_real).atoms(*_SIGN_BLIND):
            negative |= {back.get(symbol, symbol) for symbol in function.free_symbols}
    negative = {symbol for symbol in negative if not symbol.is_nonnegative}
    complex_ = {symbol for symbol in complex_ - real if not symbol.is_extended_real}
    return negative, complex_


def _off_positive_values(symbols: list[sp.Symbol], trial: int, negative: set, complex_: set) -> dict:
    """A positive trial with some of ``negative`` made negative and every one of ``complex_`` complex.

    The signs cycle through every nonzero pattern over three symbols at a time,
    so each trial has a negative value in it and pairs of negatives occur too.
    """
    values = _trial_values(symbols, trial)
    signed = [symbol for symbol in symbols if symbol in negative]
    width = min(len(signed), 3)
    pattern = trial % (2**width - 1) + 1
    for i, symbol in enumerate(signed):
        if ((pattern >> (i % 3)) & 1) ^ ((i // 3) & 1):
            values[symbol] = -values[symbol]
    for i, symbol in enumerate(symbol for symbol in symbols if symbol in complex_):
        num, den = _TRIALS[(trial + i + 1) % len(_TRIALS)]
        values[symbol] += (-1) ** (trial + i) * sp.I * sp.Rational(den, num)
    return values


def _first_difference(a: sp.Expr, b: sp.Expr, trials: list[dict]) -> tuple[int, dict | None, sp.Expr | None]:
    """How many trials could be evaluated, and the first whose values make ``a`` and ``b`` differ."""
    evaluated = 0
    for values in trials:
        try:
            diff = sp.nsimplify(sp.expand(a.subs(values) - b.subs(values)))
        except (ZeroDivisionError, TypeError, ValueError):
            continue
        if diff.has(sp.zoo, sp.nan, sp.oo):  # hit a pole; try other values
            continue
        evaluated += 1
        if sp.simplify(diff) != 0:
            return evaluated, values, diff
    return evaluated, None, None


def _named(symbols: set) -> str:
    names = sorted(str(symbol) for symbol in symbols)
    return f"{', '.join(names)} {'is' if len(names) == 1 else 'are'}"


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
        return EquivalenceResult(False, "symbolic", None, f"constants differ by {printed(delta)}")

    trials = [_trial_values(symbols, trial) for trial in range(len(_TRIALS))]
    evaluated, values, diff = _first_difference(a, b, trials)
    if values is not None:
        return EquivalenceResult(
            False, "numeric", values,
            f"differ by {printed(diff)} at the counterexample",
        )

    if evaluated == 0:
        return EquivalenceResult(
            False, "numeric", None,
            "every trial hit a pole or failed to evaluate; inconclusive",
        )

    # Agreement at positive values is no evidence for these symbols. Negative
    # values first, then complex ones; each stage has to evaluate somewhere,
    # because one that only hit poles has not tested what it was there to test.
    negative, complex_ = _hidden_from_positive_trials(a, b)
    stages = [("positive", "negative", set())] if negative else []
    if complex_:
        stages.append(("real", "complex", complex_))
    for agreed, kind, to_complex in stages:
        trials = [_off_positive_values(symbols, trial, negative, to_complex)
                  for trial in range(len(_TRIALS))]
        count, values, diff = _first_difference(a, b, trials)
        if values is not None:
            where = to_complex or {symbol for symbol in negative if values[symbol].is_negative}
            return EquivalenceResult(
                False, "numeric", values,
                f"agree at every {agreed} value tried, but differ by {printed(diff)} at the "
                f"counterexample, where {_named(where)} {kind}",
            )
        if count == 0:
            return EquivalenceResult(
                False, "numeric", None,
                f"agreed on {evaluated} random trials, but no trial where "
                f"{_named(to_complex or negative)} {kind} could be evaluated -- each "
                f"hit a pole or failed; inconclusive",
            )
        evaluated += count

    # Symbolic could not prove it, but every numeric trial agreed.
    tried_off = (f", including negative{' and complex' if complex_ else ''} values of "
                 f"{', '.join(sorted(map(str, negative)))}" if negative else "")
    return EquivalenceResult(
        True, "numeric", None,
        f"agreed on {evaluated} random trials{tried_off}; simplify left {printed(delta)}",
    )
