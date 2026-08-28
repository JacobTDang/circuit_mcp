"""Launch the local Circuit Command Center on http://localhost:2300."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402


if __name__ == "__main__":
    uvicorn.run("circuit_mcp.web:app", host="127.0.0.1", port=2300, reload=False)
