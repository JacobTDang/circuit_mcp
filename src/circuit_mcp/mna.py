"""Setup check: does a student's system of equations describe the circuit?

**Not** a structural diff against lcapy's own equations. There are many correct
formulations of the same circuit -- nodal or mesh, either sign convention,
equations scaled or reordered or added together -- so a diff flags
correct-but-different work as an error. That is the single worst thing this
tool can do, so the comparison is by *meaning* instead:

1. lcapy solves the circuit, giving the true value of every unknown.
2. Substituted into a correct equation, those values must reduce it to ``0 == 0``.
   An equation that survives that substitution is a wrong equation.
3. Separately, the Jacobian of the system with respect to the unknowns must
   have full rank, or the student is a law short of pinning the circuit down.

Both are needed. Step 2 alone passes ``0 == 0`` repeated; step 3 alone passes a
complete system of confidently wrong equations.

Two silent traps are guarded here, because both produce a plausible wrong
verdict rather than an exception:

* lcapy's symbols carry assumptions, and its ``s`` is not ``sympy.Symbol('s')``
  either (it is ``complex=True, finite=True``). Symbols are therefore unified
  *by name* onto lcapy's own objects before anything is substituted, and the
  substitution itself goes through :func:`~circuit_mcp.symbols.safe_subs`,
  which raises rather than quietly no-opping.
* A circuit with no excitation solves to all-zero, which satisfies every
  homogeneous system ever written. That is refused, not reported as correct.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sympy as sp
from lcapy import Circuit

from .equivalence import equivalent
from .symbols import safe_subs

# lcapy raises these when a netlist is unbuildable or unsolvable. OSError is in
# the list because lcapy treats a single-line string as a *filename*, so a
# garbled one-line netlist surfaces as FileNotFoundError.
_LCAPY_ERRORS = (ValueError, KeyError, RuntimeError, OSError)


class CircuitError(ValueError):
    """lcapy could not build or solve the netlist."""


class SetupInputError(ValueError):
    """The equations or unknowns do not line up with the circuit."""


@dataclass(frozen=True)
class SetupResult:
    ok: bool
    kind: str  # "ok" | "not_satisfied" | "underdetermined" | "error"
    message: str
    failing_equation: int | None = None   # 0-based index into the caller's list
    counterexample: dict | None = None


@dataclass(frozen=True)
class _Solution:
    """The circuit's true answer, plus the namespace it is expressed in."""

    circuit: Circuit
    domain: str
    node_voltages: dict[str, sp.Expr]
    branch_currents: dict[str, sp.Expr]
    opaque_currents: list[str] = field(default_factory=list)

    @property
    def values(self) -> dict[str, sp.Expr]:
        return {**self.node_voltages, **self.branch_currents}


# ---------------------------------------------------------------------------
# lcapy
# ---------------------------------------------------------------------------

def _build(netlist: str) -> Circuit:
    try:
        return Circuit(netlist)
    except _LCAPY_ERRORS as exc:
        raise CircuitError(f"lcapy could not read this netlist: {exc}") from exc


def _part(superposition, domain: str) -> sp.Expr:
    """One domain out of an lcapy superposition; an empty one is exactly zero."""
    if not superposition:
        return sp.S.Zero
    return sp.sympify(superposition[domain].sympy)


def _solve(circuit: Circuit) -> _Solution:
    """Node voltages and branch currents, in whichever single domain applies."""
    try:
        voltages = {node: circuit[node].V for node in circuit.node_list}
        currents = {}
        opaque = []
        for name in circuit.branch_list:
            try:
                currents[name] = circuit[name].I
            except KeyError:
                # lcapy models the `opamp` variant without an accessible branch
                # current. Record it rather than pretending the branch is absent.
                opaque.append(name)
    except _LCAPY_ERRORS as exc:
        raise CircuitError(f"lcapy could not solve this circuit: {exc}") from exc

    domains = {
        key
        for superposition in (*voltages.values(), *currents.values())
        for key in superposition.keys()
    }
    if not domains:
        raise CircuitError(
            "This circuit has no excitation, so every voltage and current "
            "solves to zero -- which would satisfy any set of homogeneous "
            "equations. Add a source before checking a setup."
        )
    if len(domains) > 1:
        raise CircuitError(
            f"The solution is a superposition across {sorted(map(str, domains))}. "
            f"Checking a setup needs one domain; drive the circuit with a "
            f"single source kind."
        )
    domain = domains.pop()

    return _Solution(
        circuit=circuit,
        domain=str(domain),
        node_voltages={
            f"V{node}": _part(superposition, domain)
            for node, superposition in voltages.items()
        },
        branch_currents={
            f"I_{name}": _part(superposition, domain)
            for name, superposition in currents.items()
        },
        opaque_currents=opaque,
    )


def _unknown_name(expr) -> str:
    """lcapy names an s-domain nodal unknown ``V1(s)``; the node is ``V1``."""
    inner = expr.sympy
    if isinstance(inner, sp.core.function.AppliedUndef):
        return inner.func.__name__
    return str(inner)


# ---------------------------------------------------------------------------
# circuit_equations
# ---------------------------------------------------------------------------

def circuit_equations(netlist: str) -> dict:
    """lcapy's own nodal/MNA system, rendered for display and comparison.

    ``equations`` is empty when lcapy cannot form the system -- it refuses
    circuits containing dependent sources, which includes every op-amp. The
    solved values are always present, and they are what the setup check
    actually needs, so ``note`` explains the gap instead of hiding it.
    """
    circuit = _build(netlist)
    solution = _solve(circuit)

    notes = []
    if solution.opaque_currents:
        notes.append(
            f"lcapy does not expose a branch current for "
            f"{', '.join(solution.opaque_currents)}."
        )

    equations: list[sp.Eq] = []
    display: list[str] = []
    unknowns: list[str] = []
    matrix: dict | None = None

    if circuit.dependent_sources:
        notes.insert(0, (
            f"lcapy's nodal analysis does not handle dependent source(s) "
            f"{', '.join(circuit.dependent_sources)}, so no equation system is "
            f"available for this circuit -- only the solved values below."
        ))
        unknowns = [
            name for name in solution.node_voltages if name != "V0"
        ]
    else:
        try:
            nodal = circuit.nodal_analysis()
            for value in nodal.nodal_equations().values():
                lhs, rhs = sp.sympify(value.lhs.sympy), sp.sympify(value.rhs.sympy)
                equations.append(sp.Eq(lhs, rhs, evaluate=False))
                display.append(f"{lhs} = {rhs}")
            unknowns = [_unknown_name(u) for u in nodal.unknowns]
            matrix = {"A": sp.Matrix(nodal.A), "b": sp.Matrix(nodal.b)}
        except _LCAPY_ERRORS as exc:
            raise CircuitError(
                f"lcapy could not form the nodal system: {exc}"
            ) from exc

    return {
        "domain": solution.domain,
        "unknowns": unknowns,
        "equations": equations,
        "display": display,
        "matrix": matrix,
        "node_voltages": dict(solution.node_voltages),
        "branch_currents": dict(solution.branch_currents),
        "note": " ".join(notes),
    }


# ---------------------------------------------------------------------------
# check_setup
# ---------------------------------------------------------------------------

def _residual(equation, index: int) -> sp.Expr:
    """``lhs - rhs``, i.e. the quantity a correct setup drives to zero."""
    if isinstance(equation, sp.logic.boolalg.BooleanTrue):
        return sp.S.Zero
    if isinstance(equation, sp.logic.boolalg.BooleanFalse):
        return sp.S.One  # never zero, so it is reported as unsatisfied
    if isinstance(equation, sp.Equality):
        return sp.sympify(equation.lhs) - sp.sympify(equation.rhs)
    if isinstance(equation, sp.Expr):
        return equation  # a bare expression means "= 0"
    raise SetupInputError(
        f"Equation {index} is a {type(equation).__name__}, not an equality. "
        f"Pass sympy.Eq(lhs, rhs), or a bare expression meaning '= 0'."
    )


def _aliases(solution: _Solution) -> dict[str, str | None]:
    """Accepted spellings -> canonical name, or None where two names collide."""
    table: dict[str, str | None] = {}

    def offer(alias: str, canonical: str) -> None:
        if alias in table and table[alias] != canonical:
            table[alias] = None
            return
        table[alias] = canonical

    for name in solution.node_voltages:                     # "V1"
        offer(name, name)
        offer(f"V_{name[1:]}", name)                        # "V_1"
    for name in solution.branch_currents:                   # "I_R1"
        offer(name, name)
        offer(f"I{name[2:]}", name)                         # "IR1"
    return table


def _resolve(solution: _Solution, unknowns: list[str]) -> dict[str, sp.Expr]:
    """Map each declared unknown onto its true value, or say why it cannot be."""
    if not unknowns:
        raise SetupInputError("No unknowns supplied; nothing to check against.")
    duplicates = {n for n in unknowns if unknowns.count(n) > 1}
    if duplicates:
        raise SetupInputError(f"Unknown(s) listed twice: {sorted(duplicates)}.")

    table = _aliases(solution)
    resolved = {}
    for name in unknowns:
        canonical = table.get(name, "missing")
        if canonical is None:
            raise SetupInputError(
                f"{name!r} is ambiguous in this circuit -- it names more than "
                f"one quantity."
            )
        if canonical == "missing":
            raise SetupInputError(
                f"{name!r} names nothing in this circuit. Node voltages are "
                f"{sorted(solution.node_voltages)}; branch currents are "
                f"{sorted(solution.branch_currents)}."
            )
        resolved[name] = solution.values[canonical]
    return resolved


def _canonical_symbols(
    solution: _Solution, residuals: list[sp.Expr], unknowns: list[str]
) -> dict[str, sp.Symbol]:
    """One symbol object per name, shared by the circuit and the equations.

    lcapy's objects win every name they define: they are what appears inside
    the true values, so substituting against anything else would silently do
    nothing. Names lcapy does not define -- the unknowns, and whatever else the
    student introduced -- keep the first object seen in the equations.
    """
    canonical: dict[str, sp.Symbol] = {}
    for residual in residuals:
        for symbol in sorted(residual.free_symbols, key=str):
            canonical.setdefault(str(symbol), symbol)

    from_lcapy: dict[str, sp.Symbol] = {
        name: symbol
        for name, symbol in solution.circuit.symbols.items()
        if isinstance(symbol, sp.Symbol)
    }
    for value in solution.values.values():
        for symbol in value.free_symbols:
            from_lcapy[str(symbol)] = symbol

    collisions = sorted(set(unknowns) & set(from_lcapy))
    if collisions:
        raise SetupInputError(
            f"Unknown(s) {collisions} share a name with a circuit symbol, so "
            f"substituting the solution would be circular. Rename the unknown."
        )

    canonical.update(from_lcapy)
    return canonical


def _unify(residual: sp.Expr, canonical: dict[str, sp.Symbol]) -> sp.Expr:
    """Rewrite every symbol onto the canonical object of the same name."""
    replacements = {
        symbol: canonical[str(symbol)]
        for symbol in residual.free_symbols
        if str(symbol) in canonical and canonical[str(symbol)] is not symbol
    }
    return residual.xreplace(replacements) if replacements else residual


def _is_zero(expr: sp.Expr):
    """(satisfied, counterexample, inconclusive) for ``expr == 0``."""
    if sp.cancel(sp.together(expr)) == 0:
        return True, None, False
    verdict = equivalent(expr, sp.S.Zero)
    if verdict.equivalent:
        return True, None, False
    # equivalence.py reports "not equal, numeric, no counterexample" only when
    # every trial hit a pole -- that is undecided, not a wrong equation.
    inconclusive = verdict.oracle == "numeric" and verdict.counterexample is None
    return False, verdict.counterexample, inconclusive


def check_setup(netlist: str, equations: list[sp.Eq], unknowns: list[str]) -> SetupResult:
    """Does this system of equations describe this circuit?

    ``unknowns`` names the quantities the system solves for: node voltages as
    ``V1`` / ``V_1``, branch currents as ``I_R1`` / ``IR1``. Branch current
    follows lcapy's convention -- into the first node named for that element in
    the netlist -- so the opposite convention reads as a sign error, and the
    message says which direction was assumed.

    Node voltages are referenced to node ``0``. A setup written against a
    different reference node describes a different set of unknowns and is not
    recognised.
    """
    try:
        circuit = _build(netlist)
        solution = _solve(circuit)
        true_values = _resolve(solution, unknowns)

        if not equations:
            raise SetupInputError("No equations supplied; nothing to check.")
        residuals = [_residual(eq, i) for i, eq in enumerate(equations)]
        canonical = _canonical_symbols(solution, residuals, unknowns)
    except (CircuitError, SetupInputError) as exc:
        return SetupResult(False, "error", str(exc))

    residuals = [_unify(residual, canonical) for residual in residuals]
    # An unknown no equation mentions still needs a symbol, so that it shows up
    # as an empty Jacobian column rather than vanishing from the rank test.
    unknown_symbols = [
        canonical.setdefault(name, sp.Symbol(name)) for name in unknowns
    ]

    # 1. Every equation must be satisfied by the circuit's actual solution.
    for index, residual in enumerate(residuals):
        substituted = residual
        for name, symbol in zip(unknowns, unknown_symbols):
            if symbol in substituted.free_symbols:
                substituted = safe_subs(substituted, symbol, true_values[name])

        satisfied, counterexample, inconclusive = _is_zero(substituted)
        if inconclusive:
            return SetupResult(
                False,
                "error",
                f"Equation {index} could not be evaluated at any trial point, "
                f"so whether it holds is undecided. Left with {substituted}.",
                failing_equation=index,
            )
        if not satisfied:
            hint = ""
            if any(name.startswith("I") for name in unknowns):
                hint = (
                    " Branch current is taken positive into the first node "
                    "named for that element in the netlist -- check the "
                    "direction if the magnitude looks right."
                )
            return SetupResult(
                False,
                "not_satisfied",
                f"Equation {index} is not satisfied by the circuit's actual "
                f"solution ({solution.domain}-domain), so it does not describe "
                f"this circuit.{hint}",
                failing_equation=index,
                counterexample=counterexample,
            )

    # 2. And the system must pin the unknowns down, not merely be consistent.
    jacobian = sp.Matrix(residuals).jacobian(unknown_symbols)

    nonlinear = [
        (row, col)
        for row in range(jacobian.rows)
        for col in range(jacobian.cols)
        if jacobian[row, col].has(*unknown_symbols)
    ]
    if nonlinear:
        row, _ = nonlinear[0]
        return SetupResult(
            False,
            "error",
            f"Equation {row} is nonlinear in the unknowns, so a rank test "
            f"would only linearise it. This check covers linear systems.",
            failing_equation=row,
        )

    rank = jacobian.rank(simplify=True)
    if rank < len(unknowns):
        absent = [
            name
            for index, name in enumerate(unknowns)
            if all(jacobian[row, index] == 0 for row in range(jacobian.rows))
        ]
        detail = (
            f" No equation involves {', '.join(absent)}."
            if absent
            else " Some of them restate each other."
        )
        return SetupResult(
            False,
            "underdetermined",
            f"Every equation holds, but {len(equations)} of them give rank "
            f"{rank} against {len(unknowns)} unknowns, so the system does not "
            f"determine a unique solution -- you are {len(unknowns) - rank} "
            f"equation(s) short.{detail}",
        )

    return SetupResult(
        True,
        "ok",
        f"All {len(equations)} equation(s) hold for the circuit's actual "
        f"solution, and the system has full rank {rank} for "
        f"{len(unknowns)} unknown(s).",
    )
