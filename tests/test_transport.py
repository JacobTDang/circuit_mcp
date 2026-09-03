"""End-to-end checks through the configured stdio MCP transport.

The rest of the suite calls Python tool functions directly. These tests launch
the exact command in ``.mcp.json``, negotiate a real MCP session, and cross the
JSON-RPC/stdio boundary. They protect the harness wiring that direct tests
cannot see, including source-checkout imports and structured result delivery.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]

DIVIDER = """
Vs 1 0 {V}
R1 1 2 {R1}
R2 2 0 {R2}
"""


def _configured_server(data_dir: Path) -> StdioServerParameters:
    config = json.loads((ROOT / ".mcp.json").read_text())
    circuit = config["mcpServers"]["circuit"]
    return StdioServerParameters(
        command=str(ROOT / circuit["command"]),
        args=[str(ROOT / arg) if arg == "run_server.py" else arg for arg in circuit["args"]],
        cwd=str(ROOT),
        env={**circuit.get("env", {}), "CIRCUIT_MCP_DATA_DIR": str(data_dir)},
    )


async def _exercise_server(data_dir: Path) -> None:
    async with stdio_client(_configured_server(data_dir)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "derive",
                "check_equivalence",
                "check_derivation",
                "circuit_equations",
                "check_setup",
                "workspace_status",
                "capture_workspace",
                "ipad_capture_status",
                "ipad_receiver_start",
                "ipad_receiver_stop",
                "capture_ipad_screen",
                "ocr_status",
                "transcribe_image",
                "transcribe_workspace",
                "configure_workspace",
                "workspace_configuration",
                "simulate_spice",
                "characterize_transfer",
                "converter_metrics",
                "spectrum_metrics",
                "quantize",
                "opamp_limits",
                "visual_status",
                "visual_generate",
                "visual_list",
                "visual_get",
                "visual_preview",
                "canvas_card_add",
                "canvas_card_list",
                "canvas_card_remove",
                "import_waveform_csv",
                "instrument_status",
                "instrument_query",
                "rectifier_metrics",
                "bjt_emitter_follower",
                "relaxation_oscillator",
                "dac_output",
                "alias_frequency",
                "transimpedance",
                "library_search",
                "document_get",
                "problem_get",
                "study_context",
                "attempt_history",
                "course_progress",
                "problem_create",
                "problem_update_interpretation",
                "transcription_confirm",
                "attempt_create",
                "attempt_complete",
                "problem_tag",
            }

            # Reachable without a renderer: both report honestly instead of pretending.
            renderer = await session.call_tool("visual_status", {})
            assert renderer.is_error is False
            assert renderer.structured_content["ok"] is True
            assert isinstance(renderer.structured_content["can_author"], bool)

            visuals = await session.call_tool("visual_list", {})
            assert visuals.is_error is False
            assert visuals.structured_content["items"] == []

            derived = await session.call_tool(
                "derive",
                {
                    "netlist": DIVIDER,
                    "in_pos": "1",
                    "in_neg": "0",
                    "out_pos": "2",
                    "out_neg": "0",
                    "mode": "finite",
                },
            )
            assert derived.is_error is False
            assert derived.structured_content["ok"] is True
            assert derived.structured_content["transfer_function"]["text"] == (
                "R2/(R1 + R2)"
            )

            checked = await session.call_tool(
                "check_setup",
                {
                    "netlist": DIVIDER,
                    "equations": ["V1 = V", "(V1 - V2)/R1 = V2/R2"],
                    "unknowns": ["V1", "V2"],
                },
            )
            assert checked.is_error is False
            assert checked.structured_content["ok"] is True
            assert [
                role["role"] for role in checked.structured_content["equation_roles"]
            ] == ["law", "law"]

            status = await session.call_tool("workspace_status", {})
            assert status.is_error is False
            assert status.structured_content["ok"] is True
            assert status.structured_content["platform"] == "macos"

            refused = await session.call_tool(
                "capture_workspace", {"display": 0}
            )
            assert refused.is_error is False
            assert refused.structured_content["ok"] is False
            assert refused.structured_content["error"] == "capture_error"

            ocr = await session.call_tool("ocr_status", {"load_model": False})
            assert ocr.is_error is False
            assert ocr.structured_content["backend"] == "unimernet"

            bad_image = await session.call_tool(
                "transcribe_image", {"image_base64": "not base64!"}
            )
            assert bad_image.is_error is False
            assert bad_image.structured_content["error"] == "bad_image"

            characterized = await session.call_tool(
                "characterize_transfer", {"expression": "1/(s + 1)"}
            )
            assert characterized.structured_content["stable"] is True
            assert characterized.structured_content["poles"][0]["real"] == -1

            converter = await session.call_tool(
                "converter_metrics",
                {"kind": "adc", "bits": 2, "values": [0.25, 0.5, 0.75]},
            )
            assert converter.structured_content["max_abs_inl_lsb"] == 0

            # Sixteen samples, one coherent cycle, and no harmonics.
            spectrum = await session.call_tool(
                "spectrum_metrics",
                {
                    "samples": [
                        0.0, 0.3826834324, 0.7071067812, 0.9238795325,
                        1.0, 0.9238795325, 0.7071067812, 0.3826834324,
                        0.0, -0.3826834324, -0.7071067812, -0.9238795325,
                        -1.0, -0.9238795325, -0.7071067812, -0.3826834324,
                    ],
                    "sample_rate": 16,
                    "fundamental_hz": 1,
                },
            )
            assert spectrum.structured_content["harmonics"][0]["amplitude_peak"] == pytest.approx(1)

            quantized = await session.call_tool(
                "quantize", {"values": [0, 0.26, 0.99], "bits": 2}
            )
            assert quantized.structured_content["codes"] == [0, 1, 3]

            limits = await session.call_tool(
                "opamp_limits",
                {"gain": 10, "noise_gain": 10, "gbw_hz": 1e6,
                 "slew_rate_v_s": 5e5, "output_peak_v": 10, "signal_hz": 1e4},
            )
            assert limits.structured_content["slew_limited"] is True

            waveform = await session.call_tool(
                "import_waveform_csv",
                {"csv_text": "t,v\n0,0\n0.001,1\n0.002,0\n",
                 "time_column": "t", "value_columns": ["v"]},
            )
            assert waveform.structured_content["sample_rate_hz"] == pytest.approx(1000)

            instruments = await session.call_tool("instrument_status", {})
            assert instruments.structured_content["enabled"] is True
            blocked_query = await session.call_tool(
                "instrument_query", {"resource": "TCPIP::scope::INSTR", "query": "VOLT 5"}
            )
            assert blocked_query.structured_content["error"] == "instrument_error"

            spice_tf = await session.call_tool(
                "simulate_spice",
                {"netlist": "V1 in 0 1\nR1 in out 1k\nR2 out 0 1k",
                 "analysis": "tf v(out) V1"},
            )
            assert spice_tf.structured_content["points"][0]["v(transfer_function)"] == pytest.approx(0.5)

            created = await session.call_tool(
                "problem_create",
                {"title": "Transport RC pole", "topic": "filters",
                 "prompt": "Find the pole", "status": "draft"},
            )
            problem_id = created.structured_content["problem"]["id"]
            updated = await session.call_tool(
                "problem_update_interpretation",
                {"problem_id": problem_id, "circuit_interpretation": "series R, shunt C",
                 "status": "confirmed"},
            )
            assert updated.structured_content["problem"]["status"] == "confirmed"
            attempt = await session.call_tool(
                "attempt_create", {"problem_id": problem_id, "actor": "codex"},
            )
            completed = await session.call_tool(
                "attempt_complete",
                {"attempt_id": attempt.structured_content["attempt"]["id"],
                 "answer": "-1/RC", "status": "correct"},
            )
            assert completed.structured_content["attempt"]["status"] == "correct"
            progress = await session.call_tool("course_progress", {})
            assert progress.structured_content["problems"] == {"confirmed": 1}
            context = await session.call_tool("study_context", {"query": "RC"})
            assert context.structured_content["problems"][0]["id"] == problem_id


def test_configured_stdio_server_solves_a_problem_end_to_end(tmp_path):
    asyncio.run(_exercise_server(tmp_path / "mcp-data"))
