"""Representative EE 230 questions through the real MCP transport.

Expected formulas are stated independently in the cases below. Each derived
answer crosses stdio/JSON-RPC and is then compared through a separate MCP call,
so this protects parameter routing as well as the circuit implementation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]

CASES = (
    (
        "numeric 1 kohm / 2 kohm divider",
        "Vs 1 0 {1}\nR1 1 2 {1000}\nR2 2 0 {2000}",
        "2",
        "finite",
        "2/3",
        0,
    ),
    (
        "resistive voltage divider",
        "Vs 1 0 {V}\nR1 1 2 {R1}\nR2 2 0 {R2}",
        "2",
        "finite",
        "R2/(R1 + R2)",
        0,
    ),
    (
        "RC low-pass",
        "Vs 1 0 s {V}\nR1 1 2 {R}\nC1 2 0 {C}",
        "2",
        "finite",
        "1/(1 + s*R*C)",
        1,
    ),
    (
        "RC high-pass",
        "Vs 1 0 s {V}\nC1 1 2 {C}\nR1 2 0 {R}",
        "2",
        "finite",
        "s*R*C/(1 + s*R*C)",
        1,
    ),
    (
        "ideal inverting op-amp",
        "Vs 1 0 {V}\nRi 1 2 {Ri}\nRf 2 3 {Rf}\nE1 3 0 opamp 0 2 {A}",
        "3",
        "ideal",
        "-Rf/Ri",
        0,
    ),
    (
        "ideal non-inverting op-amp",
        "Vs 1 0 {V}\nRg 2 0 {Rg}\nRf 3 2 {Rf}\nE1 3 0 opamp 1 2 {A}",
        "3",
        "ideal",
        "1 + Rf/Rg",
        0,
    ),
    (
        "finite gain-bandwidth inverting op-amp",
        "Vs 1 0 {V}\nRi 1 2 {Ri}\nRf 2 3 {Rf}\nE1 3 0 opamp 0 2 {A}",
        "3",
        "gbw",
        "-A0*Rf*wp/(A0*Ri*wp + Rf*s + Rf*wp + Ri*s + Ri*wp)",
        1,
    ),
    (
        "series RLC low-pass",
        "Vs 1 0 s {V}\nR1 1 2 {R}\nL1 2 3 {L}\nC1 3 0 {C}",
        "3",
        "finite",
        "1/(L*C*s^2 + R*C*s + 1)",
        2,
    ),
    (
        "RL low-pass",
        "Vs 1 0 s {V}\nL1 1 2 {L}\nR1 2 0 {R}",
        "2", "finite", "R/(R + s*L)", 1,
    ),
    (
        "RL high-pass",
        "Vs 1 0 s {V}\nR1 1 2 {R}\nL1 2 0 {L}",
        "2", "finite", "s*L/(R + s*L)", 1,
    ),
    (
        "series RLC intermediate-node response",
        "Vs 1 0 s {V}\nL1 1 2 {L}\nR1 2 3 {R}\nC1 3 0 {C}",
        "2", "finite", "(1 + s*R*C)/(1 + s*R*C + s^2*L*C)", 2,
    ),
    (
        "series RLC high-pass",
        "Vs 1 0 s {V}\nR1 1 2 {R}\nC1 2 3 {C}\nL1 3 0 {L}",
        "3", "finite", "s^2*L*C/(1 + s*R*C + s^2*L*C)", 2,
    ),
    (
        "ideal voltage follower",
        "Vs 1 0 {V}\nE1 2 0 opamp 1 2 {A}",
        "2", "ideal", "1", 0,
    ),
    (
        "finite-gain non-inverting amplifier",
        "Vs 1 0 {V}\nRg 2 0 {Rg}\nRf 3 2 {Rf}\nE1 3 0 opamp 1 2 {A}",
        "3", "finite", "A*(Rf + Rg)/(A*Rg + Rf + Rg)", 0,
    ),
)


async def _check_questions() -> None:
    parameters = StdioServerParameters(
        command=str(ROOT / ".venv/bin/python"),
        args=[str(ROOT / "run_server.py")],
        cwd=str(ROOT),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, netlist, output, mode, expected, pole_count in CASES:
                derived = await session.call_tool(
                    "derive",
                    {
                        "netlist": netlist,
                        "in_pos": "1",
                        "in_neg": "0",
                        "out_pos": output,
                        "out_neg": "0",
                        "mode": mode,
                    },
                )
                assert derived.is_error is False, name
                result = derived.structured_content
                assert result["ok"] is True, (name, result)
                assert len(result["poles"]) == pole_count, name

                comparison = await session.call_tool(
                    "check_equivalence",
                    {
                        "expr_a": result["transfer_function"]["text"],
                        "expr_b": expected,
                    },
                )
                assert comparison.is_error is False, name
                assert comparison.structured_content["equivalent"] is True, (
                    name,
                    comparison.structured_content,
                )

            wrong_step = await session.call_tool(
                "check_derivation",
                {
                    "steps": [
                        "R2/(R1 + R2)",
                        "R2/R1 + R2",
                    ],
                    "truth": "R2/(R1 + R2)",
                },
            )
            assert wrong_step.structured_content["kind"] == "algebra"
            assert wrong_step.structured_content["step_index"] == 0

            wrong_mode = await session.call_tool(
                "derive",
                {
                    "netlist": CASES[0][1],
                    "in_pos": "1",
                    "in_neg": "0",
                    "out_pos": "2",
                    "out_neg": "0",
                    "mode": "unsupported",
                },
            )
            assert wrong_mode.structured_content["ok"] is False
            assert wrong_mode.structured_content["error"] == "bad_mode"

            spice_cases = (
                ("divider operating point", "V1 in 0 10\nR1 in out 1k\nR2 out 0 2k", "op", ["v(out)"], 1),
                ("divider DC sweep", "V1 in 0 0\nR1 in out 1k\nR2 out 0 1k", "dc V1 0 4 1", ["v(out)"], 5),
                ("RC AC response", "V1 in 0 AC 1\nR1 in out 1k\nC1 out 0 1u", "ac dec 3 10 10k", ["frequency", "v(out)"], 10),
                ("RC transient", "V1 in 0 PULSE(0 1 0 1n 1n 10m 20m)\nR1 in out 1k\nC1 out 0 1u", "tran 20u 1m", ["time", "v(out)"], 1),
                ("nonlinear diode", "V1 in 0 5\nR1 in diode 1k\nD1 diode 0 DIO\n.model DIO D(Is=1e-14)", "op", ["v(diode)"], 1),
            )
            for name, deck, analysis, outputs, minimum_points in spice_cases:
                simulated = await session.call_tool(
                    "simulate_spice",
                    {"netlist": deck, "analysis": analysis, "outputs": outputs},
                )
                assert simulated.is_error is False, name
                result = simulated.structured_content
                assert result["ok"] is True, (name, result)
                assert len(result["points"]) >= minimum_points, name
                if name == "divider operating point":
                    assert abs(result["points"][0]["v(out)"] - 20 / 3) < 1e-12
                elif name == "divider DC sweep":
                    assert result["points"][-1]["v(out)"] == 2.0
                elif name == "RC AC response":
                    first = result["points"][0]["v(out)"]
                    last = result["points"][-1]["v(out)"]
                    assert first["real"] > 0.99
                    assert abs(last["imag"]) > abs(last["real"])
                elif name == "RC transient":
                    assert abs(result["points"][-1]["v(out)"] - 0.63212) < 0.001
                elif name == "nonlinear diode":
                    assert 0.5 < result["points"][0]["v(diode)"] < 0.8


def test_representative_ee230_questions_over_mcp():
    asyncio.run(_check_questions())
