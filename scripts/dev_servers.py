"""Run the API and frontend together, stopping both when either one exits."""

from __future__ import annotations

import errno
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def available_api_port() -> int:
    """Prefer the usual API port, falling back to a free local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", 8765))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


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
        port = available_api_port()
        env = os.environ.copy()
        env["GROUNDTRUTH_API_PORT"] = str(port)
        if port != 8765:
            print(f"API port 8765 is in use; using {port} instead.", flush=True)
        for command in (
            ["uv", "run", "python", "-m", "src.web.server", "--port", str(port)],
            ["npm", "--prefix", "frontend", "run", "dev"],
        ):
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True))

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
