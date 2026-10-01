"""Exercise the same HTTP endpoints the React frontend calls."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from src.providers import ProviderConfig
from src.web.server import create_server
from src.web.service import AgentService


def _request(base_url: str, path: str, session_id: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={
            "X-Session-ID": session_id,
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_frontend_api_health_state_scenario_ask_and_reset(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<main>GroundTruth</main>")
    server = create_server(port=0, web_root=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    session_id = str(uuid4())
    try:
        with urllib.request.urlopen(f"{base_url}/", timeout=5) as page:
            assert page.status == 200
            assert b"GroundTruth" in page.read()

        with urllib.request.urlopen(f"{base_url}/graph", timeout=5) as graph_page:
            assert graph_page.status == 200
            assert b"GroundTruth" in graph_page.read()

        status, health = _request(base_url, "/api/health", session_id)
        assert status == 200
        assert health["provider"] == "RuleBasedIntentProvider"

        status, initial = _request(base_url, "/api/state", session_id)
        assert status == 200
        assert initial["graph"]["node_count"] == 17
        assert initial["graph"]["edge_count"] == 17
        assert initial["history"] == []

        status, loaded = _request(base_url, "/api/scenarios/scenario-a", session_id, {})
        assert status == 200
        assert loaded["environment"]["obstacles"]["obs_01"]["y_cm"] == 12.0
        assert loaded["graph"]["node_count"] == 17
        assert loaded["graph"]["edge_count"] >= 20
        assert {"obs_01", "obs_02"} <= set(loaded["environment"]["obstacles"])
        assert {"pallet_04", "box_02"} <= set(loaded["environment"]["objects"])

        status, answered = _request(
            base_url,
            "/api/ask",
            session_id,
            {"question": loaded["suggested_question"]},
        )
        assert status == 200
        assert answered["history"][-1]["status"] == "grounded_conflict_resolved"
        assert any(
            edge["source"] == "route_A"
            and edge["predicate"] == "status_is"
            and edge["target"] == "blocked"
            for edge in answered["graph"]["edges"]
        )

        status, scenario_b = _request(base_url, "/api/scenarios/scenario-b", session_id, {})
        assert status == 200
        assert scenario_b["environment"]["light"]["color_cast"] == "yellow"
        assert scenario_b["graph"]["node_count"] == 17
        assert scenario_b["graph"]["edge_count"] >= 20
        assert {"box_01", "box_02", "shelf_01"} <= set(scenario_b["environment"]["objects"])
        status, perspectives = _request(
            base_url,
            "/api/ask",
            session_id,
            {"question": scenario_b["suggested_question"]},
        )
        assert status == 200
        latest = perspectives["history"][-1]
        assert latest["perspectives"]["user_perspective"].startswith("Red")
        assert latest["perspectives"]["egocentric_perspective"].startswith("Brown")
        assert latest["perspectives"]["historical_perspective"].startswith("Blue")
        assert latest["sensor_telemetry"][0]["measurements"]["object_id"] == "box_01"

        status, reset = _request(base_url, "/api/reset", session_id, {})
        assert status == 200
        assert reset["graph"]["node_count"] == 17
        assert reset["graph"]["edge_count"] == 17
        assert reset["history"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_rejects_invalid_session_and_empty_question(tmp_path) -> None:
    server = create_server(port=0, web_root=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        status, bad_session = _request(base_url, "/api/state", "not-a-uuid")
        assert status == 400
        assert bad_session["code"] == "invalid_session"

        session_id = str(uuid4())
        status, empty = _request(base_url, "/api/ask", session_id, {"question": "   "})
        assert status == 400
        assert empty["code"] == "invalid_request"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_returns_503_when_live_mode_unconfigured(tmp_path) -> None:
    from src.providers import ProviderConfig
    from src.web.service import AgentService

    unconfigured_svc = AgentService(config=ProviderConfig(mode="live", api_key=None))
    server = create_server(port=0, web_root=tmp_path, service=unconfigured_svc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    session_id = str(uuid4())
    try:
        status, health = _request(base_url, "/api/health", session_id)
        assert status == 200
        assert health["status"] == "degraded"
        assert health["llm_ready"] is False
        assert "GROQ_API_KEY is not configured" in health["error"]

        status, error_resp = _request(
            base_url, "/api/ask", session_id, {"question": "Is the route clear?"}
        )
        assert status == 503
        assert error_resp["code"] == "provider_unavailable"
        assert "GROQ_API_KEY is not configured" in error_resp["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_returns_503_when_configured_live_provider_request_fails(tmp_path: Path) -> None:
    from src.agent import GroundedAgent
    from src.providers.groq_provider import GroqIntentProvider

    (tmp_path / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")

    class FailingClient:
        def create_chat_completion(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("Groq upstream service error 503")

    failing_provider = GroqIntentProvider(client=FailingClient())
    configured_svc = AgentService(
        config=ProviderConfig(mode="live", api_key="test-api-key-placeholder")
    )

    session_id = str(uuid4())
    session = configured_svc.get_session(session_id)
    session.agent = GroundedAgent.create(provider=failing_provider)

    server = create_server(port=0, web_root=tmp_path, service=configured_svc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        status, error_resp = _request(
            base_url,
            "/api/ask",
            session_id,
            {"question": "Is the route clear?"},
        )
        assert status == 503
        assert error_resp["code"] == "provider_unavailable"
        assert "Live intent provider failed" in error_resp["error"]
        assert "Groq upstream service error 503" in error_resp["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_returns_503_when_grounded_response_provider_call_fails(tmp_path: Path) -> None:
    from src.agent import GroundedAgent
    from src.providers.groq_provider import GroqIntentProvider

    class FailsOnGroundedResponse:
        def __init__(self) -> None:
            self.calls = 0

        def create_chat_completion(self, *args: object, **kwargs: object) -> object:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("upstream timeout during response generation")
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_unsupported",
                                    "function": {
                                        "name": "unsupported",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }

    (tmp_path / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    provider = GroqIntentProvider(client=FailsOnGroundedResponse())
    configured_svc = AgentService(
        config=ProviderConfig(mode="live", api_key="test-api-key-placeholder")
    )
    session_id = str(uuid4())
    configured_svc.get_session(session_id).agent = GroundedAgent.create(provider=provider)

    server = create_server(port=0, web_root=tmp_path, service=configured_svc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        status, response = _request(
            base_url,
            "/api/ask",
            session_id,
            {"question": "What is the room temperature?"},
        )
        assert status == 503
        assert response["code"] == "provider_unavailable"
        assert "grounded explanation call failed" in response["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
