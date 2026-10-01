"""Run the local JSON API and serve the built browser UI."""

from __future__ import annotations

import argparse
import json
import logging
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.procedural.intent_provider import IntentProviderUnavailableError
from src.web.service import AgentService

LOGGER = logging.getLogger("groundtruth.web")
MAX_REQUEST_BYTES = 16_384
DEFAULT_WEB_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class AgentRequestHandler(SimpleHTTPRequestHandler):
    """HTTP routes for one local GroundTruth app process."""

    def __init__(self, *args, service: AgentService, web_root: Path, **kwargs) -> None:
        self.service = service
        self.web_root = web_root
        super().__init__(*args, directory=str(web_root), **kwargs)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(200, self.service.health())
            return
        if path == "/api/state":
            try:
                session = self.service.get_session(self.headers.get("X-Session-ID", ""))
                with session.lock:
                    self._send_json(200, session.snapshot())
            except ValueError as exc:
                self._send_error_json(400, "invalid_session", str(exc))
            except RuntimeError as exc:
                self._send_error_json(503, "session_limit", str(exc))
            except Exception:
                LOGGER.exception("Failed to read agent session state")
                self._send_error_json(
                    500, "state_read_failed", "Could not read agent state. Check backend logs."
                )
            return
        if path.startswith("/api/"):
            self._send_error_json(404, "not_found", "API endpoint not found.")
            return
        if not (self.web_root / "index.html").is_file():
            self._send_error_json(
                503,
                "frontend_not_built",
                "Frontend build is missing. Run `npm install` and `npm run build` first.",
            )
            return
        if path == "/graph":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if not path.startswith("/api/"):
            self._send_error_json(404, "not_found", "API endpoint not found.")
            return
        if path not in {
            "/api/ask",
            "/api/reset",
            "/api/scenarios/scenario-a",
            "/api/scenarios/scenario-b",
        }:
            self._send_error_json(404, "not_found", "API endpoint not found.")
            return

        try:
            payload = self._read_json_body()
            session = self.service.get_session(self.headers.get("X-Session-ID", ""))
            with session.lock:
                if path == "/api/ask":
                    question = payload.get("question")
                    if not isinstance(question, str):
                        raise ValueError("'question' must be a string.")
                    session.ask(question)
                elif path == "/api/reset":
                    session.reset()
                elif path in ("/api/scenarios/scenario-a", "/api/scenarios/scenario-b"):
                    scenario = path.rsplit("/", 1)[-1]
                    session.load_scenario(scenario)
                else:
                    self._send_error_json(404, "not_found", "API endpoint not found.")
                    return
                self._send_json(200, session.snapshot())
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error_json(400, "invalid_request", str(exc))
        except IntentProviderUnavailableError as exc:
            self._send_error_json(503, "provider_unavailable", str(exc))
        except RuntimeError as exc:
            self._send_error_json(503, "session_limit", str(exc))
        except Exception:
            LOGGER.exception("Agent request failed")
            self._send_error_json(
                500, "agent_request_failed", "Agent request failed. Check backend logs."
            )

    def _read_json_body(self) -> dict[str, object]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("Content-Length header is required.")
        try:
            size = int(content_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer.") from exc
        if size < 0 or size > MAX_REQUEST_BYTES:
            raise ValueError(f"Request body must be at most {MAX_REQUEST_BYTES} bytes.")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        raw_body = self.rfile.read(size)
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object.")
        return payload

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": message, "code": code})

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    web_root: Path = DEFAULT_WEB_ROOT,
    service: AgentService | None = None,
) -> ThreadingHTTPServer:
    """Create a server instance, also used by API integration tests."""
    svc = service or AgentService()
    handler = partial(AgentRequestHandler, service=svc, web_root=web_root)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GroundTruth local app server.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost).")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(args.host, args.port)
    print(f"GroundTruth app server listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping GroundTruth app server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
