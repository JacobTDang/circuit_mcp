"""Symbol binding — guards trap 1 from the spike.

lcapy's symbols carry assumptions, so sympy.Symbol('A') is a different object.
subs() against a bare Symbol silently returns the expression unchanged. A
substitution that quietly does nothing is the exact failure this tool exists to
prevent, so it must raise instead.
"""
import pytest
import sympy as sp

from circuit_mcp.symbols import bind, safe_subs, SubstitutionError


def lcapy_style_expr():
    """An expression whose symbols carry assumptions, as lcapy produces."""
    A, Ri, Rf = sp.symbols("A Ri Rf", positive=True)
    return -A * Rf / (A * Ri + Rf + Ri)


def test_bind_returns_symbols_by_name():
    expr = lcapy_style_expr()
    names = bind(expr)
    assert set(names) == {"A", "Ri", "Rf"}
    assert names["A"].assumptions0["positive"] is True


def test_safe_subs_raises_on_silent_noop():
    """The trap: a bare Symbol does not match an assumption-carrying one."""
    expr = lcapy_style_expr()
    bare = sp.Symbol("A")  # no assumptions -> will not match
    with pytest.raises(SubstitutionError, match="did not change"):
        safe_subs(expr, bare, sp.Integer(5))


def test_safe_subs_succeeds_when_bound_by_name():
    expr = lcapy_style_expr()
    A = bind(expr)["A"]
    out = safe_subs(expr, A, sp.Integer(5))
    assert out != expr
    assert "A" not in {str(x) for x in out.free_symbols}


def test_safe_subs_reports_the_symbol_it_could_not_find():
    expr = lcapy_style_expr()
    with pytest.raises(SubstitutionError, match="Q"):
        safe_subs(expr, sp.Symbol("Q"), sp.Integer(1))
