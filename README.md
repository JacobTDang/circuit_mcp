# circuit_mcp

An MCP server that checks circuit derivations against a symbolic ground truth,
for Iowa State EE 2300 (Electronic Circuits and Systems).

It does not solve homework. Given a netlist and an ordered derivation, it finds
**where** the work diverges — setup errors checked against the circuit's MNA
system, algebra errors checked step-to-step, final answer against ground-truth
`H(s)`.

The model never does the math. `lcapy` and `ngspice` are the oracle.

## Status

Design only. Nothing implemented yet.

One question is open and gates the design: whether `lcapy` accepts a symbolic
`s`-dependent VCVS gain, which is required to model finite op-amp
gain-bandwidth (`A(s) = A₀ / (1 + s/ωₚ)`).

Design notes: [`docs/2026-08-24-design.md`](docs/2026-08-24-design.md)

## Planned stack

- [lcapy](https://github.com/mph-/lcapy) — symbolic linear circuit analysis (SymPy)
- [ngspice](https://ngspice.sourceforge.io/) — numeric SPICE, for the nonlinear half
- Python 3.11+ in a venv (system Python is 3.9)
