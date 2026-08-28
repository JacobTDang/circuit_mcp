"""Create and verify a consistent local SQLite command-center backup."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from circuit_mcp.storage import CommandCenterDB, default_data_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", nargs="?", type=Path)
    args = parser.parse_args()
    data = default_data_dir()
    destination = args.destination or data / "backups" / time.strftime("circuit-mcp-%Y%m%d-%H%M%S.sqlite3")
    database = CommandCenterDB(data / "circuit_mcp.sqlite3")
    database.prepare(data / "library.json", data / "history.jsonl")
    print(json.dumps({"database": database.integrity(), "files": database.audit_files(),
                      "backup": database.backup(destination)}, indent=2))


if __name__ == "__main__":
    main()
