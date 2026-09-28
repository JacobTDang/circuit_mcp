"""Launch the local Circuit Command Center on http://localhost:2300, or on the port given by --port."""
from __future__ import annotations

import argparse
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Circuit Command Center.")
    parser.add_argument("--port", type=int, default=2300,
                        help="the port to serve on; linkC passes the port it chose for its tab")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    from circuit_mcp import paths
    from circuit_mcp.app_server import EXIT_LOCKED, DataFolderLocked, acquire_data_lock

    try:
        data_lock = acquire_data_lock(paths.data_dir())  # noqa: F841 -- held until this process exits
    except DataFolderLocked as refused:
        print(refused, file=sys.stderr)
        sys.exit(EXIT_LOCKED)
    # A bounded graceful shutdown: an open page keeps a connection alive, and without a bound
    # uvicorn waits for it forever, so SIGTERM would never finish.
    uvicorn.run("circuit_mcp.web:app", host="127.0.0.1", port=args.port, reload=False,
                timeout_graceful_shutdown=3)
