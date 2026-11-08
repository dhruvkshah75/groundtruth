"""Run the local JSON API and serve the built browser UI."""

from __future__ import annotations

import argparse
import json
import logging
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from src.contracts.simulation_api import (
    SimulationApiError,
    SimulationControlRequest,
    SimulationDisruptRequest,
    SimulationEvidenceRequest,
    SimulationLoadRequest,
    SimulationRunIdentity,
)
from src.procedural.intent_provider import IntentProviderUnavailableError
from src.web.service import (
    AgentService,
    SessionUnavailable,
    SimulationExportNotReady,
    SimulationInputRejected,
    SimulationStateConflict,
    SimulationUnavailable,
    StaleRequestError,
)
from src.web.session_store import SessionStore

LOGGER = logging.getLogger("groundtruth.web")
MAX_REQUEST_BYTES = 16_384
DEFAULT_WEB_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "dist"
SIMULATION_POST = {
    "/api/simulation/load": (SimulationLoadRequest, AgentService.load_warehouse),
    "/api/simulation/control": (SimulationControlRequest, AgentService.apply_simulation_control),
    "/api/simulation/disrupt": (SimulationDisruptRequest, AgentService.disrupt_simulation),
    "/api/simulation/evidence": (SimulationEvidenceRequest, AgentService.read_simulation_evidence),
}


class AgentRequestHandler(SimpleHTTPRequestHandler):
    """HTTP routes for one local GroundTruth app process."""

    def __init__(self, *args, service: AgentService, web_root: Path, **kwargs) -> None:
        self.service = service
        self.web_root = web_root
        super().__init__(*args, directory=str(web_root), **kwargs)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/api/simulation/state", "/api/simulation/export"):
            self._simulation_request(path)
            return
        if path == "/api/health":
            self._send_json(200, self.service.health())
            return
        if path == "/api/state":
            try:
                session = self.service.get_session(self.headers.get("X-Session-ID", ""))
                with session.lock:
                    session.touch()
                    snapshot = session.snapshot()
                self._send_json(200, snapshot)
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
        if path in ("/graph", "/simulation", "/simulation/"):
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path in SIMULATION_POST:
            self._simulation_request(path)
            return
        if not path.startswith("/api/"):
            self._send_error_json(404, "not_found", "API endpoint not found.")
            return
        if path not in {
            "/api/ask",
            "/api/reset",
            "/api/new-chat",
            "/api/scenarios/scenario-a",
            "/api/scenarios/scenario-b",
        }:
            self._send_error_json(404, "not_found", "API endpoint not found.")
            return

        try:
            payload = self._read_json_body()
            session = self.service.get_session(self.headers.get("X-Session-ID", ""))
            answer = None
            if path == "/api/ask":
                question = payload.get("question")
                if not isinstance(question, str):
                    raise ValueError("'question' must be a string.")
                if "expected" in payload:
                    expected = SimulationRunIdentity.model_validate(payload["expected"])
                    answer = session.ask(question, expected=expected)
                else:
                    answer = session.ask(question)
            else:
                with session.lock:
                    session.touch()
                    if path == "/api/new-chat":
                        session.new_chat()
                    elif path == "/api/reset":
                        session.reset()
                    else:
                        session.load_scenario(path.rsplit("/", 1)[-1])
            self.service.save_session(self.headers.get("X-Session-ID", ""), session)
            with session.lock:
                session.require_active()
                snapshot = session.snapshot()
                if answer is not None:
                    snapshot["answer"] = answer
            self._send_json(200, snapshot)
        except StaleRequestError as exc:
            self._send_error_json(409, "stale_request", str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error_json(400, "invalid_request", str(exc))
        except IntentProviderUnavailableError as exc:
            cause: BaseException | None = exc
            status = None
            while cause is not None:
                status = getattr(cause, "status_code", None)
                if status in (413, 429):
                    break
                cause = cause.__cause__
            if status == 413:
                self._send_error_json(
                    503,
                    "provider_request_too_large",
                    "Request limit exceeded.",
                )
            elif status == 429:
                self._send_error_json(
                    503,
                    "provider_rate_limited",
                    "Rate limit exceeded.",
                )
            elif str(exc).startswith("GROQ_API_KEY is not configured."):
                self._send_error_json(
                    503,
                    "provider_unavailable",
                    "GROQ_API_KEY is not configured. Configure the provider or use offline mode.",
                )
            else:
                self._send_error_json(
                    503,
                    "provider_unavailable",
                    "Answer service unavailable.",
                )
        except RuntimeError as exc:
            self._send_error_json(503, "session_limit", str(exc))
        except Exception:
            LOGGER.exception("Agent request failed")
            self._send_error_json(
                500, "agent_request_failed", "Agent request failed. Check backend logs."
            )

    def _simulation_request(self, path: str) -> None:
        try:
            session_id = str(UUID(self.headers.get("X-Session-ID", "")))
        except ValueError:
            self._send_simulation_error(
                400, "invalid_session", "X-Session-ID must be a valid UUID."
            )
            return

        attachment = None
        try:
            if path in SIMULATION_POST:
                model, action = SIMULATION_POST[path]
                try:
                    request = model.model_validate(self._read_json_body())
                except (ValueError, RecursionError):
                    self._send_simulation_error(
                        400, "invalid_request", "Invalid simulation request."
                    )
                    return
                result = action(self.service, session_id, request)
            elif path == "/api/simulation/export":
                result = self.service.export_simulation(session_id)
                attachment = f'attachment; filename="groundtruth-replay-{result.run_id}.json"'
            else:
                result = self.service.read_live_simulation(session_id)
            payload = result if isinstance(result, dict) else result.model_dump(mode="json")
        except SimulationInputRejected as exc:
            self._send_simulation_error(400, "invalid_request", str(exc))
        except StaleRequestError:
            self._send_simulation_error(409, "stale_run", "The simulation run or session changed.")
        except SimulationStateConflict as exc:
            self._send_simulation_error(409, "state_conflict", str(exc))
        except SimulationExportNotReady as exc:
            self._send_simulation_error(503, "export_not_ready", str(exc))
        except IntentProviderUnavailableError:
            self._send_simulation_error(503, "provider_unavailable", "The provider is unavailable.")
        except (SimulationUnavailable, SessionUnavailable):
            LOGGER.warning("Simulation request could not complete")
            self._send_simulation_error(
                503, "runtime_unavailable", "The simulation could not complete the operation."
            )
        except Exception:
            LOGGER.error("Unexpected simulation API failure")
            self._send_simulation_error(500, "simulation_failed", "The simulation request failed.")
        else:
            self._send_json(200, payload, attachment=attachment)

    def _send_simulation_error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, SimulationApiError(code=code, error=message).model_dump())

    def do_DELETE(self) -> None:
        if urlsplit(self.path).path != "/api/session":
            self._send_error_json(404, "not_found", "API endpoint not found.")
            return
        try:
            self.service.delete_session(self.headers.get("X-Session-ID", ""))
            self._send_json(200, {"deleted": True})
        except ValueError as exc:
            self._send_error_json(400, "invalid_session", str(exc))

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
        if len(raw_body) != size:
            raise ValueError("Incomplete request body.")
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object.")
        return payload

    def _send_json(self, status: int, payload: object, *, attachment: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if attachment:
            self.send_header("Content-Disposition", attachment)
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": message, "code": code})

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()


class AgentHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, service: AgentService) -> None:
        super().__init__(address, handler)
        self.service = service

    def server_close(self) -> None:
        super().server_close()
        self.service.shutdown()


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    web_root: Path = DEFAULT_WEB_ROOT,
    service: AgentService | None = None,
) -> ThreadingHTTPServer:
    """Create a server instance, also used by API integration tests."""
    svc = service or AgentService()
    handler = partial(AgentRequestHandler, service=svc, web_root=web_root)
    server = AgentHTTPServer((host, port), handler, svc)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GroundTruth local app server.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost).")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = SessionStore(Path("data/chat-sessions.sqlite"))
    server = create_server(args.host, args.port, service=AgentService(store=store))
    print(f"GroundTruth app server listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping GroundTruth app server.")
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
