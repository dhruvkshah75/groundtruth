"""Run the API and frontend together, stopping both when either one exits."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Start both development servers and return their exit status."""
    processes: list[subprocess.Popen[bytes]] = []
    interrupted = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for command in (
            ["uv", "run", "python", "-m", "src.web.server"],
            ["npm", "--prefix", "frontend", "run", "dev"],
        ):
            processes.append(subprocess.Popen(command, cwd=ROOT, start_new_session=True))

        while not interrupted:
            for process in processes:
                status = process.poll()
                if status is not None:
                    print("A development server stopped; stopping the other server.", flush=True)
                    return status if status > 0 else 1
            time.sleep(0.2)
        return 0
    finally:
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    sys.exit(main())
