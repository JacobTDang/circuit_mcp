"""Opt-in, read-only VISA discovery and SCPI queries for laboratory equipment."""
from __future__ import annotations

import os
import re

import pyvisa


class InstrumentError(ValueError):
    """Instrument access is disabled, unsafe, unavailable, or failed."""


_ENABLED = "CIRCUIT_MCP_ENABLE_INSTRUMENTS"
_RESOURCE = re.compile(r"[A-Za-z0-9_:./+-]{1,256}")
_QUERY = re.compile(
    r"(?:\*IDN|\*ESR|SYST(?:EM)?:ERR(?:OR)?|MEAS(?:URE)?:[A-Za-z0-9_:]+|"
    r"FETC(?:H)?|READ|WAV(?:EFORM)?:PRE(?:AMBLE)?|WAV(?:EFORM)?:DATA)\?",
    re.I,
)


def instrument_status() -> dict:
    enabled = os.environ.get(_ENABLED) == "1"
    result = {"ok": True, "backend": "pyvisa-py", "enabled": enabled, "resources": []}
    if not enabled:
        result["note"] = f"Set {_ENABLED}=1 in the MCP environment to permit read-only discovery."
        return result
    try:
        manager = pyvisa.ResourceManager("@py")
        try:
            result["resources"] = list(manager.list_resources())
        finally:
            manager.close()
    except Exception as exc:
        raise InstrumentError(f"VISA discovery failed: {type(exc).__name__}: {exc}") from exc
    return result


def instrument_query(resource: str, query: str, timeout_ms: int = 5000) -> dict:
    if os.environ.get(_ENABLED) != "1":
        raise InstrumentError(f"instrument access is disabled; set {_ENABLED}=1 explicitly")
    if _RESOURCE.fullmatch(resource) is None:
        raise InstrumentError("resource contains unsupported characters or is too long")
    command = " ".join(query.split())
    if _QUERY.fullmatch(command) is None:
        raise InstrumentError("only allow-listed read-only SCPI queries are accepted")
    if not 100 <= timeout_ms <= 30_000:
        raise InstrumentError("timeout_ms must be between 100 and 30,000")
    try:
        manager = pyvisa.ResourceManager("@py")
        try:
            instrument = manager.open_resource(resource)
            try:
                instrument.timeout = timeout_ms
                response = instrument.query(command)
            finally:
                instrument.close()
        finally:
            manager.close()
    except Exception as exc:
        raise InstrumentError(f"VISA query failed: {type(exc).__name__}: {exc}") from exc
    if len(response) > 5_000_000:
        raise InstrumentError("instrument response exceeds 5,000,000 characters")
    return {"ok": True, "resource": resource, "query": command, "response": response.strip()}
