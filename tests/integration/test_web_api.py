"""Exercise the same HTTP endpoints the React frontend calls."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from uuid import uuid4

from src.web.server import create_server


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

        status, health = _request(base_url, "/api/health", session_id)
        assert status == 200
        assert health["provider"] == "RuleBasedIntentProvider"

        status, initial = _request(base_url, "/api/state", session_id)
        assert status == 200
        assert initial["ledger"] == []
        assert initial["history"] == []

        status, loaded = _request(base_url, "/api/scenarios/scenario-a", session_id, {})
        assert status == 200
        assert loaded["environment"]["obstacles"]["obs_01"]["y_cm"] == 12.0

        status, answered = _request(
            base_url,
            "/api/ask",
            session_id,
            {"question": loaded["suggested_question"]},
        )
        assert status == 200
        assert answered["history"][-1]["status"] == "grounded_conflict_resolved"
        assert answered["graph"]["edges"][0]["target"] == "blocked"

        status, scenario_b = _request(base_url, "/api/scenarios/scenario-b", session_id, {})
        assert status == 200
        assert scenario_b["environment"]["light"]["color_cast"] == "yellow"
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
        assert reset["ledger"] == []
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
