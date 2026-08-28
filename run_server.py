"""Source-checkout entry point for the circuit MCP server.

The project lives in an iCloud-synchronised directory where hidden editable-
install ``.pth`` files can be ignored by Python. Add ``src`` explicitly so the
checked-in MCP command works from a fresh checkout and does not depend on that
fragile interpreter-side registration.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from circuit_mcp.server import main  # noqa: E402 -- path bootstrap precedes import


if __name__ == "__main__":
    main()
