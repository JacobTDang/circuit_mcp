"""Finite gain-bandwidth despite lcapy rejecting s in the gain field.

Derive with a constant symbol A, then substitute A -> A(s) afterwards.
Valid because the MNA solve treats A as an opaque symbol either way.
"""
import warnings
warnings.filterwarnings("ignore")

from lcapy import Circuit
import sympy as sp

H_const = Circuit("""
Vs 1 0 {V}
Ri 1 2 {Ri}
Rf 2 3 {Rf}
E1 3 0 opamp 0 2 {A}
""").transfer(1, 0, 3, 0).sympy

# lcapy's symbols carry assumptions, so Symbol('A') != lcapy's A. Bind by name.
sym = {str(x): x for x in H_const.free_symbols}
print("free symbols and assumptions:")
for n, x in sorted(sym.items()):
    print(f"   {n:4} {x.assumptions0}")

A, Ri, Rf = sym['A'], sym['Ri'], sym['Rf']
A0, wp = sp.symbols('A0 wp', positive=True)
# s must carry NO assumptions: positive=True makes poles (which are
# negative) silently unsolvable -- sp.solve returns [] instead of erroring.
s = sp.Symbol('s')

print(f"\nderived with constant A:\n  {H_const}")

H_sub = sp.cancel(sp.simplify(H_const.subs(A, A0 / (1 + s/wp))))
print(f"\nafter substituting A(s):\n  {H_sub}")

H_truth = -A0*Rf / (A0*Ri + (Rf + Ri)*(1 + s/wp))
print(f"\nhand-derived ground truth:\n  {sp.cancel(H_truth)}")

# Oracle 1: symbolic
delta = sp.simplify(sp.cancel(sp.together(H_sub - H_truth)))
print(f"\nsymbolic delta: {delta}    MATCH: {delta == 0}")

# Oracle 2: random numeric (the safety net for when simplify is indecisive)
subs = {A0: sp.Rational(100000), wp: sp.Rational(63), Ri: sp.Rational(1000),
        Rf: sp.Rational(10000), s: sp.Rational(377)}
d = sp.N(sp.expand(H_sub.subs(subs) - H_truth.subs(subs)))
print(f"numeric delta:  {d}    MATCH: {abs(complex(d)) < 1e-30}")

# The physics EE 2300 actually asks for
print(f"\nDC gain (s->0):    {sp.simplify(H_sub.subs(s, 0))}")
pole = sp.solve(sp.denom(H_sub), s)
print(f"closed-loop pole:  {sp.simplify(pole[0])}")
print("  -> pole = wp*(1 + A0*Ri/(Ri+Rf)) : the gain-bandwidth tradeoff")

# srepr round-trip, for the serialization boundary if this ever goes over HTTP
rt = sp.sympify(sp.srepr(H_sub))
print(f"\nsrepr round-trip exact: {sp.simplify(rt - H_sub) == 0}")
