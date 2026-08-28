from __future__ import annotations

import pytest

from circuit_mcp.course_metrics import opamp_limits
from circuit_mcp.instruments import InstrumentError, instrument_query, instrument_status
from circuit_mcp.lab import LabDataError, import_waveform_csv


def test_opamp_bandwidth_and_slew_limits_match_closed_form():
    result = opamp_limits(10, 10, 1e6, 0.5e6, 10, 10_000)
    assert result["closed_loop_bandwidth_hz"] == 100_000
    assert result["full_power_bandwidth_hz"] == pytest.approx(7957.747)
    assert result["required_slew_rate_v_s"] == pytest.approx(2 * 3.141592653589793 * 1e5)
    assert result["bandwidth_limited"] is False
    assert result["slew_limited"] is True


def test_scope_csv_import_computes_sample_rate_and_preserves_channels():
    result = import_waveform_csv("Time,CH1,CH2\n0,1,2\n0.001,2,3\n0.002,3,4\n", "Time", ["CH1", "CH2"])
    assert result["rows"] == 3
    assert result["sample_rate_hz"] == pytest.approx(1000)
    assert result["uniform"] is True
    assert result["columns"]["CH1"] == [1, 2, 3]


def test_scope_csv_rejects_bad_columns_non_numeric_and_nonmonotonic_time():
    with pytest.raises(LabDataError, match="missing"):
        import_waveform_csv("t,a\n0,1\n1,2\n", "time", ["a"])
    with pytest.raises(LabDataError, match="not numeric"):
        import_waveform_csv("t,a\n0,one\n1,2\n", "t", ["a"])
    with pytest.raises(LabDataError, match="strictly increasing"):
        import_waveform_csv("t,a\n1,1\n0,2\n", "t", ["a"])


def test_instruments_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CIRCUIT_MCP_ENABLE_INSTRUMENTS", raising=False)
    assert instrument_status()["enabled"] is False
    with pytest.raises(InstrumentError, match="disabled"):
        instrument_query("TCPIP::scope::INSTR", "*IDN?")


def test_instrument_rejects_write_commands_before_opening_hardware(monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_ENABLE_INSTRUMENTS", "1")
    with pytest.raises(InstrumentError, match="read-only"):
        instrument_query("TCPIP::scope::INSTR", "VOLT 5")


def test_allowlisted_query_closes_instrument_and_manager(monkeypatch):
    events = []

    class FakeInstrument:
        timeout = None
        def query(self, command):
            events.append(("query", command, self.timeout))
            return "ACME,SCOPE,123,1.0\n"
        def close(self): events.append(("instrument_close",))

    class FakeManager:
        def open_resource(self, resource):
            events.append(("open", resource))
            return FakeInstrument()
        def close(self): events.append(("manager_close",))

    monkeypatch.setenv("CIRCUIT_MCP_ENABLE_INSTRUMENTS", "1")
    monkeypatch.setattr("circuit_mcp.instruments.pyvisa.ResourceManager", lambda _: FakeManager())
    result = instrument_query("TCPIP::scope::INSTR", "*IDN?", 1234)
    assert result["response"] == "ACME,SCOPE,123,1.0"
    assert events == [
        ("open", "TCPIP::scope::INSTR"),
        ("query", "*IDN?", 1234),
        ("instrument_close",),
        ("manager_close",),
    ]
