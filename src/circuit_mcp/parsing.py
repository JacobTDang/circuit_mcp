"""The string boundary: untrusted text -> SymPy.

Expressions arrive over the wire as strings. ``sympy.sympify()`` evaluates its
argument, so it is an arbitrary-code-execution hole on untrusted input. This
module is the only place a string is allowed to become an expression.

``parse_expr`` with a restricted ``global_dict`` is *not* sufficient on its own.
Two things leak:

* Python's ``eval`` injects the real ``__builtins__`` into any globals mapping
  that lacks the key, so the mapping must set ``__builtins__`` to ``{}``
  explicitly.
* Even then, ``sqrt.__globals__`` hands back the SymPy module's own globals --
  which do hold the real builtins -- and ``().__class__.__base__.__subclasses__()``
  reaches every class in the process. Both were verified live against SymPy
  1.14. So the input is screened *before* it is parsed, and that screen, not the
  restricted namespace, is the primary defence.

The screen is an allowlist of characters plus a small set of named rejections.
An allowlist is used because SymPy hands the text to Python's tokenizer, which
NFKC-normalises identifiers: six Unicode characters fold to ``_``, so
``sqrt._<U+FF3F>globals_<U+FF3F>`` contains no ASCII ``__`` yet evaluates as
``sqrt.__globals__``. A blocklist over raw text cannot see that; restricting the
input to ASCII removes the question. The cost is that Unicode symbol names
(``ω0``) are rejected -- write ``w0``, which is what the rest of this codebase
uses anyway.

Transformations enabled, and why:

* ``auto_symbol`` / ``auto_number`` -- required; they are what turns bare names
  and literals into SymPy objects.
* ``convert_xor`` -- ``s^2`` means a power here. Nothing in circuit analysis
  wants bitwise xor, and students write ``^`` constantly.
* ``implicit_multiplication`` -- ``s R C`` and ``2R`` are how the course writes
  products. Note this is *not* ``implicit_multiplication_application``: that one
  also turns ``f x`` into ``f(x)``, which would silently reinterpret a product
  as a function call whenever the name collides with a SymPy function (``beta``,
  ``gamma``, ``zeta`` -- all ordinary EE symbols). A silent reinterpretation is
  the failure this project cannot afford, so only the ``*`` insertion is taken.
* ``rationalize`` -- ``0.5`` becomes ``1/2``. Float dust leaves the symbolic
  oracle in ``equivalence`` undecided on expressions that are exactly equal.

Deliberately *not* enabled: ``lambda_notation`` (first entry of
``standard_transformations``; builds callables out of input) and
``factorial_notation`` (``999999!`` is a free denial of service).

Two notation collisions, called out because both would be silent:

* ``I`` is SymPy's imaginary unit, not a current, and ``E`` is Euler's number,
  not a source. They keep their SymPy meanings, because ground truth comes from
  lcapy and disagreeing with lcapy's own output would report correct work as
  wrong. Write currents as ``Ic``/``i_c``.
* ``Rf(Ri)`` is rejected rather than read as a product. Function-call syntax on
  a non-function is ambiguous, and guessing is worse than refusing.

One more trap worth recording, found while testing this module: SymPy caches
expression construction on argument *equality*, so ``Integer(2)*A`` can return a
cached expression built from a different-but-equal ``A`` than the one passed in.
``is``-identity is therefore not a usable post-condition anywhere in this
codebase; equality including assumptions is, and it is the property ``subs`` and
every comparison actually depend on. Importing lcapy clears that cache, which is
what makes the difference observable.
"""
from __future__ import annotations

import keyword
import re

import sympy as sp
from sympy.parsing.sympy_parser import (
    auto_number,
    auto_symbol,
    convert_xor,
    implicit_multiplication,
    parse_expr,
    rationalize,
)


class ParseError(ValueError):
    """The input could not be parsed, or was rejected as unsafe."""


# Long enough for an MNA-sized rational function, short enough that the
# tokenizer cannot be used as a denial of service.
_MAX_LENGTH = 10_000

# Everything an expression in this course needs, and nothing else. No quotes
# (string literals), no brackets (subscripting), no ``;`` or ``:`` (statements),
# no ``<>!`` (comparisons), and no non-ASCII (see the module docstring on NFKC).
_ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9_ \t\r\n+\-*/^().,]*$")

# SymPy's ``auto_symbol`` passes Python keywords through to ``eval`` verbatim,
# so they really do reach the interpreter. The builtins listed alongside them
# are unreachable once ``__builtins__`` is blanked, but naming them turns a
# confusing SyntaxError into an explanation.
_BANNED_NAMES = frozenset(keyword.kwlist) | frozenset({
    "eval", "exec", "compile", "open", "input", "breakpoint", "help",
    "getattr", "setattr", "delattr", "hasattr", "globals", "locals", "vars",
    "dir", "type", "object", "super", "exit", "quit",
})

_NAME_HINTS = {
    "lambda": (
        " If this is MOSFET channel-length modulation, write it as 'lam'"
        " -- 'lambda' is a Python keyword and cannot be a symbol name."
    ),
}

_NAME_PATTERN = re.compile(r"\b(" + "|".join(sorted(_BANNED_NAMES, key=len, reverse=True)) + r")\b")

# ``.`` starting an identifier is attribute access; ``.`` before a digit is a
# decimal point.
_ATTRIBUTE_PATTERN = re.compile(r"\.\s*[A-Za-z_]")

# The functions a string may call.
_FUNCTIONS: dict[str, object] = {
    "sqrt": sp.sqrt,
    "exp": sp.exp,
    "log": sp.log,
    "ln": sp.log,
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "atan2": sp.atan2,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "Abs": sp.Abs,
    "sign": sp.sign,
    "re": sp.re,
    "im": sp.im,
    "arg": sp.arg,
    "conjugate": sp.conjugate,
    "Max": sp.Max,
    "Min": sp.Min,
    "Heaviside": sp.Heaviside,
    "DiracDelta": sp.DiracDelta,
}

# ``I`` is the imaginary unit and ``E`` is Euler's number -- see the module
# docstring on the collision with EE's current and source symbols.
_CONSTANTS: dict[str, object] = {"pi": sp.pi, "I": sp.I, "E": sp.E, "oo": sp.oo}

# The only namespace a parsed string can see. ``__builtins__`` must be present
# and empty: ``eval`` silently inserts the real one otherwise.
#
# ``Symbol``/``Integer``/``Float``/``Rational`` are emitted by the
# transformations themselves, so the generated code cannot run without them.
# ``Function`` is deliberately absent: the screen rejects call syntax on an
# unknown name, so nothing should ever emit it, and its absence turns a missed
# case into a loud failure rather than an opaque undefined function.
_GLOBAL_DICT: dict[str, object] = {
    "__builtins__": {},
    "Symbol": sp.Symbol,
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Rational": sp.Rational,
    **_FUNCTIONS,
    **_CONSTANTS,
}

# A name immediately before '(' is function-call syntax.
_CALL_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")

_TRANSFORMATIONS = (
    auto_symbol,
    auto_number,
    convert_xor,
    implicit_multiplication,
    rationalize,
)


def _require_text(text: str) -> str:
    """Reject anything that is not a usable, bounded string."""
    if not isinstance(text, str):
        raise ParseError(f"Expected a string, got {type(text).__name__}.")
    if len(text) > _MAX_LENGTH:
        raise ParseError(
            f"Input is too long: {len(text)} characters, limit {_MAX_LENGTH}."
        )
    stripped = text.strip()
    if not stripped:
        raise ParseError("Input is empty.")
    return stripped


def _screen(text: str) -> None:
    """Reject constructs that could reach the interpreter. Runs before parsing."""
    banned = _NAME_PATTERN.search(text)
    if banned:
        name = banned.group(1)
        raise ParseError(
            f"Rejected {name!r}: Python keywords and builtins are passed straight "
            f"through to eval() by SymPy's parser, so they are an escape route "
            f"out of the expression language, not a symbol name."
            + _NAME_HINTS.get(name, "")
        )

    if "__" in text:
        raise ParseError(
            "Rejected '__': dunder attributes such as __class__, __subclasses__ "
            "and __globals__ reach the interpreter's own objects and are a known "
            "sandbox escape. An expression never needs them."
        )

    attribute = _ATTRIBUTE_PATTERN.search(text)
    if attribute:
        raise ParseError(
            f"Rejected attribute access at {attribute.group(0)!r}: '.' followed by "
            f"a name reads a Python object's members, which is how a parsed string "
            f"escapes into the interpreter. Only a decimal point is allowed."
        )

    if not _ALLOWED_CHARS.match(text):
        bad = sorted({c for c in text if not _ALLOWED_CHARS.match(c)})
        raise ParseError(
            f"Rejected the character(s) {bad}: expressions are restricted to "
            f"ASCII letters, digits, '_' and the operators + - * / ^ ( ) , . "
            f"Anything else -- quotes, brackets, ';', ':', comparisons, non-ASCII "
            f"-- is either a statement or a Unicode lookalike, both of which "
            f"defeat a text screen."
        )

    # SymPy's implicit multiplication reads ``A(s)`` as ``A*s``. That is a
    # silent reinterpretation of function notation, so refuse it here instead.
    unknown = sorted({
        name for name in _CALL_PATTERN.findall(text) if name not in _FUNCTIONS
    })
    if unknown:
        raise ParseError(
            f"Unknown function(s) {unknown}: a name followed by '(' is a function "
            f"call, and SymPy would quietly read it as multiplication instead. If "
            f"you meant a product write '*'; if you meant a function of s, "
            f"substitute it out. Known functions: {sorted(_FUNCTIONS)}."
        )


def _resolve_symbols(symbols: dict[str, sp.Symbol] | None) -> dict[str, sp.Symbol]:
    """Validate the caller's symbol table and copy it, since SymPy writes to it."""
    if symbols is None:
        return {}
    if not isinstance(symbols, dict):
        raise ParseError(
            f"symbols must be a dict of name -> Symbol, got {type(symbols).__name__}."
        )
    for name, symbol in symbols.items():
        if not isinstance(symbol, sp.Symbol):
            raise ParseError(
                f"symbols[{name!r}] is {type(symbol).__name__}, not a Symbol."
            )
        if str(symbol) != name:
            raise ParseError(
                f"symbols[{name!r}] is a symbol whose name is {str(symbol)!r}. "
                f"Binding a name to a differently-named symbol substitutes one "
                f"quantity for another without saying so."
            )
    # auto_symbol writes discovered names into local_dict, so hand it a copy.
    return dict(symbols)


def parse_expression(
    text: str, symbols: dict[str, sp.Symbol] | None = None
) -> sp.Expr:
    """Parse a mathematical expression into SymPy.

    ``symbols`` maps a name to the symbol it must resolve to, assumptions and
    all. Pass ``bind(ground_truth)`` here: lcapy's symbols carry assumptions,
    and a freshly parsed ``Symbol('A')`` compares unequal to lcapy's ``A`` while
    printing identically, which makes correct work look wrong.
    """
    stripped = _require_text(text)
    _screen(stripped)
    required = _resolve_symbols(symbols)
    local_dict = dict(required)  # SymPy writes discovered names into this one

    try:
        expr = parse_expr(
            stripped,
            local_dict=local_dict,
            global_dict=dict(_GLOBAL_DICT),
            transformations=_TRANSFORMATIONS,
            evaluate=True,
        )
    except Exception as exc:  # translated, not swallowed -- the cause is chained
        raise ParseError(
            f"Could not parse {text!r}: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(expr, sp.Expr):
        raise ParseError(
            f"{text!r} is not an expression -- it parsed to "
            f"{type(expr).__name__}. Use parse_equation for 'lhs = rhs'."
        )

    # The point of the symbols parameter. A mismatch here would be silent, so
    # confirm rather than trust that local_dict took effect.
    #
    # Compared by equality, not by ``is``. SymPy caches expression construction
    # on argument equality, so ``Integer(2)*A`` can hand back a cached Mul built
    # from an equal-but-distinct ``A`` -- visible whenever something clears the
    # cache mid-process, which importing lcapy does. Equality including
    # assumptions is also the right invariant on its own terms: it is what subs()
    # and every comparison in this codebase actually test.
    for symbol in expr.free_symbols:
        wanted = required.get(str(symbol))
        if wanted is not None and symbol != wanted:
            raise ParseError(
                f"{str(symbol)!r} parsed to a symbol that does not match the one "
                f"supplied ({sp.srepr(symbol)} vs {sp.srepr(wanted)}). Comparing "
                f"these would report correct work as wrong."
            )
    return expr


def parse_as_written(text: str) -> sp.Expr:
    """Parse for display only. The tree keeps the order the text was written in.

    ``parse_expression`` evaluates as it builds, and SymPy's canonical order turns
    ``V_s/R * exp(-t/(R*C))`` into ``V_s/(R*exp(t/(C*R)))`` -- equal, and not the
    form a student is trying to learn. This runs the same screen and then builds
    the tree unevaluated so a printer can show what was actually typed.

    Display only: an unevaluated tree must never be compared for equality. Use
    ``parse_expression`` for anything that decides a verdict.
    """
    stripped = _require_text(text)
    _screen(stripped)
    try:
        return parse_expr(
            stripped,
            local_dict={},
            global_dict={**_GLOBAL_DICT, "Mul": sp.Mul, "Add": sp.Add, "Pow": sp.Pow},
            transformations=_TRANSFORMATIONS,
            evaluate=False,
        )
    except Exception as exc:  # translated, not swallowed -- the cause is chained
        raise ParseError(
            f"Could not lay out {text!r} as written: {type(exc).__name__}: {exc}"
        ) from exc


def _split_on_equals(text: str) -> tuple[str, str]:
    """Split on the single top-level ``=``, refusing zero, several, or ``==``."""
    if "==" in text:
        raise ParseError(
            "Rejected '==': that is Python's comparison operator. An equation is "
            "written with a single '=' as 'lhs = rhs'."
        )

    depth = 0
    positions = []
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "=" and depth == 0:
            positions.append(index)

    if not positions:
        raise ParseError(
            f"Expected an equation 'lhs = rhs', but {text!r} contains no "
            f"top-level '='."
        )
    if len(positions) > 1:
        raise ParseError(
            f"Expected one top-level '=', but {text!r} has {len(positions)}. "
            f"A chain like 'a = b = c' is two equations; send them separately."
        )

    cut = positions[0]
    return text[:cut], text[cut + 1:]


def parse_equation(
    text: str, symbols: dict[str, sp.Symbol] | None = None
) -> sp.Eq:
    """Parse 'lhs = rhs' into a SymPy Eq."""
    stripped = _require_text(text)
    lhs, rhs = _split_on_equals(stripped)
    return sp.Eq(
        parse_expression(lhs, symbols),
        parse_expression(rhs, symbols),
        evaluate=False,
    )
