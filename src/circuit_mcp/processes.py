"""Stopping a managed child process inside a known amount of time.

The macOS app SIGTERMs the server and SIGKILLs it ten seconds later, and up to
three of those seconds belong to uvicorn closing connections before the web
lifespan runs at all. The lifespan then stops the AirPlay receiver and the
Showman worker in sequence, so it is the *sum* of those two that has to fit in
what is left. Both used to escalate over three seconds each, which made every
quit with either one running end in a SIGKILL the app reported as a crash.

Neither child holds state this process needs at quit time: the renderer's
artifacts are already on disk and the receiver has nothing to flush. So a short
grace period followed by a kill loses nothing, and the bound is what matters.
"""
from __future__ import annotations

import subprocess

GRACEFUL_SECONDS = 1.0
KILL_SECONDS = 0.5
BUDGET_SECONDS = GRACEFUL_SECONDS + KILL_SECONDS


def stop_within(process: subprocess.Popen | None,
                graceful: float = GRACEFUL_SECONDS,
                kill: float = KILL_SECONDS) -> bool:
    """Ask the process to stop, then make it. True when it is gone.

    False means the kill itself did not land inside its own wait, which is a
    stuck process the caller should say something about rather than hide: the
    group is reported, not silently left behind.
    """
    if process is None or process.poll() is not None:
        return True
    process.terminate()
    try:
        process.wait(timeout=graceful)
        return True
    except subprocess.TimeoutExpired:
        pass
    process.kill()
    try:
        process.wait(timeout=kill)
        return True
    except subprocess.TimeoutExpired:
        return False
