"""Launch the local Circuit Command Center on http://localhost:2300."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def load_local_env(path: Path = ROOT / ".env") -> None:
    """Load simple local KEY=VALUE settings without overriding the shell environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.isidentifier():
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_local_env()

import uvicorn  # noqa: E402


if __name__ == "__main__":
    uvicorn.run("circuit_mcp.web:app", host="127.0.0.1", port=2300, reload=False)
