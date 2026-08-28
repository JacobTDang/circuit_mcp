"""Bounded, local ngspice analyses for circuits outside symbolic lcapy."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


class SpiceError(ValueError):
    """The deck or analysis is unsafe, invalid, or failed to simulate."""


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+|[a-z]+)?"
_ANALYSES = {
    "op": re.compile(r"op", re.I),
    "dc": re.compile(rf"dc\s+[A-Za-z][\w.]*\s+{_NUMBER}\s+{_NUMBER}\s+{_NUMBER}", re.I),
    "ac": re.compile(rf"ac\s+(?:dec|oct|lin)\s+\d+\s+{_NUMBER}\s+{_NUMBER}", re.I),
    "tran": re.compile(rf"tran\s+{_NUMBER}\s+{_NUMBER}(?:\s+{_NUMBER})?(?:\s+{_NUMBER})?(?:\s+uic)?", re.I),
    "tf": re.compile(r"tf\s+v\([A-Za-z0-9_.+-]+(?:,[A-Za-z0-9_.+-]+)?\)\s+[A-Za-z][\w.]*", re.I),
    "pz": re.compile(r"pz\s+[A-Za-z0-9_.+-]+\s+[A-Za-z0-9_.+-]+\s+[A-Za-z0-9_.+-]+\s+[A-Za-z0-9_.+-]+\s+(?:cur|vol)\s+(?:pol|zer|pz)", re.I),
    "noise": re.compile(rf"noise\s+v\([A-Za-z0-9_.+-]+(?:,[A-Za-z0-9_.+-]+)?\)\s+[A-Za-z][\w.]*\s+(?:dec|oct|lin)\s+\d+\s+{_NUMBER}\s+{_NUMBER}(?:\s+\d+)?", re.I),
    "disto": re.compile(rf"disto\s+(?:dec|oct|lin)\s+\d+\s+{_NUMBER}\s+{_NUMBER}(?:\s+{_NUMBER})?", re.I),
    "sens": re.compile(rf"sens\s+v\([A-Za-z0-9_.+-]+(?:,[A-Za-z0-9_.+-]+)?\)(?:\s+ac\s+(?:dec|oct|lin)\s+\d+\s+{_NUMBER}\s+{_NUMBER})?", re.I),
}
_FORBIDDEN = re.compile(
    r"^\s*\.(?:control|endc|include|inc|lib|shell|exec|command|op|dc|ac|tran|tf|pz|noise|disto|sens|end)\b",
    re.I | re.M,
)
_VECTOR = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*(?:\([A-Za-z0-9_.+#-]+\))?")
MAX_POINTS = 100_000


def _validate(netlist: str, analysis: str, outputs: list[str]) -> str:
    if not isinstance(netlist, str) or not netlist.strip() or len(netlist) > 50_000:
        raise SpiceError("netlist must contain 1 to 50,000 characters")
    match = _FORBIDDEN.search(netlist)
    if match:
        raise SpiceError(f"netlist directive {match.group(0).strip()!r} is not allowed")
    command = " ".join(analysis.split())
    kind = command.partition(" ")[0].lower()
    pattern = _ANALYSES.get(kind)
    if pattern is None or pattern.fullmatch(command) is None:
        raise SpiceError(
            "analysis must be 'op', 'dc SOURCE START STOP STEP', "
            "'ac dec|oct|lin POINTS START STOP', 'tran TSTEP TSTOP [TSTART [TMAX]]', "
            "'tf v(OUT) SOURCE', 'pz IN+ IN- OUT+ OUT- cur|vol pol|zer|pz', "
            "'noise v(OUT) SOURCE dec|oct|lin POINTS START STOP', or "
            "'disto dec|oct|lin POINTS START STOP [F2/F1]', or "
            "'sens v(OUT) [ac dec|oct|lin POINTS START STOP]'"
        )
    tokens = command.split()
    if kind in {"ac", "noise", "disto"}:
        point_index = 2 if kind in {"ac", "disto"} else 4
        if int(tokens[point_index]) > 10_000:
            raise SpiceError("frequency points per interval cannot exceed 10,000")
    if kind == "sens" and len(tokens) > 2 and int(tokens[4]) > 10_000:
        raise SpiceError("frequency points per interval cannot exceed 10,000")
    for output in outputs:
        if _VECTOR.fullmatch(output) is None:
            raise SpiceError(f"invalid output vector {output!r}")
    return command


def _value(text: str) -> float | dict[str, float]:
    text = text.strip()
    if "," in text:
        real, imag = text.strip("()").split(",", 1)
        return {"real": float(real), "imag": float(imag)}
    return float(text)


def _parse_raw(path: Path, requested: list[str]) -> dict:
    lines = path.read_text(errors="replace").splitlines()
    try:
        variable_start = lines.index("Variables:") + 1
        values_start = lines.index("Values:")
    except ValueError as exc:
        raise SpiceError("ngspice did not produce a readable ASCII result") from exc
    metadata = {}
    for line in lines[:variable_start - 1]:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip().lower().replace(" ", "_")] = value.strip()
    variables = []
    for line in lines[variable_start:values_start]:
        fields = line.split()
        if len(fields) >= 3:
            variables.append({"name": fields[1].lower(), "type": fields[2].lower()})
    count = int(metadata.get("no._points", "0"))
    if count > MAX_POINTS:
        raise SpiceError(f"analysis produced {count} points; limit is {MAX_POINTS}")
    raw_values = [line.strip() for line in lines[values_start + 1:] if line.strip()]
    points = []
    cursor = 0
    for _ in range(count):
        row = {}
        for variable in variables:
            if cursor >= len(raw_values):
                raise SpiceError("ngspice result ended before all declared points")
            fields = raw_values[cursor].split()
            cursor += 1
            value = _value(fields[-1])
            # ngspice writes the frequency axis in complex-width rows and, in
            # v47, leaves the unused imaginary slot uninitialised. It is an
            # independent real variable, so do not expose that garbage value.
            if variable["type"] == "frequency" and isinstance(value, dict):
                value = value["real"]
            row[variable["name"]] = value
        if requested:
            missing = [name for name in requested if name not in row]
            if missing:
                raise SpiceError(f"unknown output vector(s): {missing}; available: {sorted(row)}")
            row = {name: row[name] for name in requested}
        points.append(row)
    return {"plot": metadata.get("plotname"), "variables": variables, "points": points}


def simulate_spice(netlist: str, analysis: str, outputs: list[str]) -> dict:
    """Run one allow-listed analysis with no access to user startup files."""
    command = _validate(netlist, analysis, outputs)
    executable = shutil.which("ngspice")
    if executable is None:
        raise SpiceError("ngspice is not installed or is not on PATH")
    requested = [name.lower() for name in outputs]
    with tempfile.TemporaryDirectory(prefix="circuit-mcp-spice-") as directory:
        root = Path(directory)
        deck = root / "circuit.cir"
        raw = root / "result.raw"
        deck.write_text(
            "circuit_mcp isolated analysis\n.option filetype=ascii\n"
            + netlist.strip() + f"\n.{command}\n.end\n"
        )
        try:
            run = subprocess.run(
                [executable, "-n", "-b", "-r", str(raw), str(deck)],
                cwd=root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
                env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            )
        except subprocess.TimeoutExpired as exc:
            raise SpiceError("ngspice exceeded the 15 second limit") from exc
        if run.returncode != 0 or not raw.exists():
            detail = (run.stderr + "\n" + run.stdout).strip()[-1200:]
            raise SpiceError(f"ngspice failed: {detail}")
        parsed = _parse_raw(raw, requested)
    return {"ok": True, "backend": "ngspice", "analysis": command, **parsed}
