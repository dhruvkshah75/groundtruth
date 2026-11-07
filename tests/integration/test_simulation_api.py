"""Real HTTP acceptance tests for the presenter API, with deterministic runtime time."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import pytest

from src.contracts.simulation_api import (
    SimulationControlRequest,
    SimulationDisruptRequest,
    SimulationDisruptResponse,
    SimulationExportBundle,
    SimulationLoadRequest,
    SimulationStateResponse,
)
from src.providers import ProviderConfig
from src.web.server import MAX_REQUEST_BYTES, create_server
from src.web.service import AgentService
from src.web.session_store import SessionStore

BASE = "/api/simulation"
PRESET = "warehouse_inspection_v1"
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def identity(state):
    return {key: state[key] for key in ("run_id", "generation")}


def load_body(state, request_id=None):
    return {"preset": PRESET, "expected": identity(state), "request_id": request_id or str(uuid4())}


def disruption(state, kind, *, at_ms=None, **kwargs):
    return {
        "expected": identity(state),
        "request_id": str(uuid4()),
        "at_ms": at_ms,
        "disruption": {"kind": kind, **kwargs},
    }


def assert_error(response, status, code):
    assert response[0] == status, response
    assert response[1]["code"] == code
    assert response[1]["error"]


def service_for(clock, store=None, exporter=None):
    ids = count(1)
    return AgentService(
        ProviderConfig(mode="rule-based", api_key=None),
        store=store,
        now=lambda: clock[0],
        epoch_now=lambda: EPOCH,
        id_factory=lambda: UUID(int=next(ids)),
        background=False,
        exporter=exporter,
    )


@contextmanager
def http_api(service, root, session_id):
    (root / "index.html").write_text("<main>GroundTruth</main>")
    server = create_server(port=0, web_root=root, service=service)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    def send(path, payload=None, *, key=session_id, method=None, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        request = Request(
            base_url + path,
            data=body if payload is not None else None,
            method=method,
            headers={"X-Session-ID": key, "Content-Type": content_type},
        )
        try:
            with urlopen(request, timeout=3) as response:
                raw = response.read()
                value = (
                    json.loads(raw)
                    if response.headers.get_content_type() == "application/json"
                    else raw
                )
                return response.status, value, response.headers
        except HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read()), exc.headers

    try:
        yield send
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def api(tmp_path):
    clock = [0.0]
    key = str(uuid4())
    service = service_for(clock)
    with http_api(service, tmp_path, key) as send:
        yield send, service, clock, key


def load(send, *, key=None):
    kwargs = {"key": key} if key is not None else {}
    code, pending, _ = send(BASE + "/state", **kwargs)
    assert code == 200
    code, loaded, _ = send(BASE + "/load", load_body(pending), **kwargs)
    assert code == 200, loaded
    return loaded


def control(send, state, action, **kwargs):
    return send(BASE + "/control", {"expected": identity(state), "action": action, **kwargs})


def test_shared_http_examples():
    path = Path(__file__).parents[1] / "fixtures/phase_two/contracts.json"
    fixtures = json.loads(path.read_text())["valid"]
    SimulationLoadRequest.model_validate(fixtures["api_load"])
    SimulationStateResponse.model_validate(fixtures["api_pending"])
    for sample in fixtures["api_controls"]:
        SimulationControlRequest.model_validate(sample)
    for sample in fixtures["api_disruptions"]:
        SimulationDisruptRequest.model_validate(sample)


def test_http_lifecycle_rates_and_legacy_compatibility(api):
    send, service, clock, _ = api
    assert b"GroundTruth" in send("/simulation")[1]
    assert b"GroundTruth" in send("/simulation/")[1]
    code, pending, headers = send(BASE + "/state")
    assert code == 200 and headers["Cache-Control"] == "no-store"
    assert pending["state"] == "pending" and pending["run_id"] is None
    assert pending["audience"] is None and pending["observation"]["latest"] == {}
    assert_error(control(send, pending, "step"), 409, "state_conflict")
    original_request = load_body(pending)
    code, paused, _ = send(BASE + "/load", original_request)
    assert code == 200
    SimulationStateResponse.model_validate(paused)
    assert paused["state"] == "paused" and paused["playback_rate"] == 0.5
    assert paused["sim_time_ms"] == paused["snapshot_sequence"] == 0
    assert {x["sample_sequence"] for x in paused["observation"]["latest"].values()} == set(range(4))
    assert paused["patrol"]["waypoint_index"] == 0
    assert send(BASE + "/load", original_request)[1] == paused

    for rate in (0.25, 0.5, 1):
        code, changed, _ = control(send, paused, "set_rate", playback_rate=rate)
        assert code == 200
        assert changed["snapshot_sequence"] == paused["snapshot_sequence"]
        assert changed["revision"] > paused["revision"]
        assert (
            changed["playback_rate"]
            == changed["audience"]["snapshot"]["config"]["playback_rate"]
            == rate
        )
        assert send(BASE + "/state")[1] == changed
        paused = changed

    code, playing, _ = control(send, paused, "play")
    assert code == 200 and playing["state"] == "running"
    assert playing["revision"] > paused["revision"] and playing["snapshot_sequence"] == 0
    clock[0] = 0.06
    assert control(send, playing, "play")[1] == playing
    assert_error(control(send, playing, "step"), 409, "state_conflict")
    clock[0] = 0.1
    service.pump()
    code, paused, _ = control(send, playing, "pause")
    assert code == 200 and paused["state"] == "paused"
    assert paused["sim_time_ms"] == 100 and paused["snapshot_sequence"] == 1
    clock[0] = 4
    service.pump()
    assert control(send, paused, "pause")[1] == send(BASE + "/state")[1] == paused
    code, stepped, _ = control(send, paused, "step")
    assert code == 200 and stepped["state"] == "paused"
    assert stepped["sim_time_ms"] == 200 and stepped["snapshot_sequence"] == 2
    assert send(BASE + "/load", original_request)[1] == stepped

    code, legacy, _ = send("/api/ask", {"question": "Where are you?"})
    assert code == 200
    assert legacy["history"][-1]["simulation_evidence"]["run_id"] == stepped["run_id"]
    assert legacy["simulation_status"]["sim_time_ms"] == 200
    assert send("/api/new-chat", {})[1]["history"] == []
    assert send("/api/state")[1]["simulation_status"]["run_id"] == stepped["run_id"]

    code, reset, _ = control(send, stepped, "reset")
    assert code == 200 and reset["state"] == "paused"
    assert reset["sim_time_ms"] == reset["snapshot_sequence"] == 0
    assert reset["run_id"] != stepped["run_id"] and reset["generation"] > stepped["generation"]
    assert reset["playback_rate"] == 1
    assert_error(control(send, stepped, "play"), 409, "stale_run")
    assert_error(send(BASE + "/load", original_request), 409, "stale_run")
    assert send(BASE + "/state")[1] == reset

    assert send("/api/scenarios/scenario-a", {})[1]["environment"]["scenario_name"].startswith(
        "Scenario A"
    )
    assert send(BASE + "/state")[1]["state"] == "pending"
    assert send("/api/scenarios/scenario-b", {})[0] == 200
    assert send("/api/reset", {})[0] == 200
    assert send("/api/session", method="DELETE")[1] == {"deleted": True}
    assert send(BASE + "/state")[1] == pending


def test_http_disruptions_use_engine_motion_and_sensor_evidence(api):
    send, service, clock, _ = api
    started = load(send)

    def x_position(state, entity_id):
        return next(
            entity["footprint"]["x_cm"]
            for entity in state["audience"]["snapshot"]["entities"]
            if entity["entity_id"] == entity_id
        )

    latest = started["observation"]["latest"]
    scene = started["audience"]["snapshot"]
    assert "parcel_03" in {obj["entity_id"] for obj in scene["entities"]}
    assert "parcel_03" not in {
        obj["object_id"] for obj in latest["camera_detect"]["result"]["measurements"]["objects"]
    }
    assert "intrinsic_color" not in json.dumps(latest)
    assert started["belief"]["recent_changes"]

    requests = [
        disruption(
            started,
            "relocate_parcel",
            entity_id="parcel_01",
            to={"x_cm": 130, "y_cm": 350},
            duration_ms=800,
        ),
        disruption(started, "cart_block", at_ms=100),
        disruption(started, "cart_clear", at_ms=1200),
        disruption(started, "lighting", at_ms=200, color_cast="yellow", intensity=0.8),
        disruption(
            started, "sensor_availability", at_ms=100, capability="lidar_scan", available=False
        ),
        disruption(
            started, "sensor_availability", at_ms=600, capability="lidar_scan", available=True
        ),
        disruption(
            started, "sensor_availability", at_ms=600, capability="camera_detect", available=False
        ),
        disruption(
            started, "sensor_availability", at_ms=1100, capability="camera_detect", available=True
        ),
    ]
    events = []
    for request in requests:
        code, receipt, _ = send(BASE + "/disrupt", request)
        assert code == 200, receipt
        SimulationDisruptResponse.model_validate(receipt)
        assert receipt["event"]["at_ms"] == (request["at_ms"] or 100)
        assert receipt["state"]["audience"] == started["audience"]
        assert receipt["state"]["observation"] == started["observation"]
        assert receipt["state"]["belief"] == started["belief"]
        events.append(receipt["event"])
    assert [event["sequence"] for event in events] == list(range(1, 9))

    playing = control(send, started, "play")[1]
    assert playing["state"] == "running"
    clock[0] = 1
    service.pump()
    _, half, _ = send(BASE + "/state")
    pose = half["audience"]["snapshot"]["robot_pose"]
    observed = half["observation"]["latest"]
    assert half["sim_time_ms"] == 500 and half["snapshot_sequence"] == 5
    assert observed["robot_pose"]["observer_pose"] == pose
    assert observed["lidar_scan"]["result"]["reason"] == "sensor_unavailable"
    assert (
        observed["camera_detect"]["result"]["measurements"]["objects"][0]["apparent_color"]
        == "brown"
    )
    assert x_position(half, "parcel_01") == 105
    assert x_position(half, "cart_01") == 148
    assert all(
        sample["run_id"] == half["run_id"] and sample["sim_time_ms"] <= 500
        for sample in observed.values()
    )
    assert send(BASE + "/disrupt", requests[0])[1]["event"] == events[0]

    clock[0] = 2
    service.pump()
    blind = send(BASE + "/state")[1]
    assert blind["sim_time_ms"] == 1000
    assert x_position(blind, "cart_01") == 108
    assert (
        blind["observation"]["latest"]["camera_detect"]["result"]["reason"] == "sensor_unavailable"
    )
    assert blind["observation"]["latest"]["lidar_scan"]["result"]["measurements"]
    clock[0] = 3
    service.pump()
    recovered = send(BASE + "/state")[1]
    assert recovered["observation"]["latest"]["camera_detect"]["result"]["measurements"]
    assert recovered["revision"] > blind["revision"] > half["revision"] > started["revision"]
    assert x_position(recovered, "cart_01") == 124
    clock[0] = 4
    service.pump()
    clock[0] = 5
    service.pump()
    assert x_position(send(BASE + "/state")[1], "cart_01") == 180

    for seconds in range(6, 61):
        clock[0] = seconds
        service.pump()
    completed = send(BASE + "/state")[1]
    assert completed["state"] == "completed" and completed["sim_time_ms"] == 30_000
    assert_error(control(send, completed, "play"), 409, "state_conflict")
    assert_error(control(send, completed, "step"), 409, "state_conflict")
    assert control(send, completed, "pause")[1] == completed
    assert_error(
        send(BASE + "/disrupt", disruption(completed, "cart_block")), 409, "state_conflict"
    )
    assert len(json.dumps(completed)) < 12_000
    assert len(completed["belief"]["recent_changes"]) <= 16
    assert {"ledger", "history", "events", "checkpoint", "api_key"}.isdisjoint(completed)
    assert send(BASE + "/state")[1] == completed
    assert control(send, completed, "reset")[1]["state"] == "paused"


def test_http_invalid_inputs_leave_run_untouched(api):
    send, service, _, key = api
    state = load(send)
    expected = identity(state)
    assert_error(send(BASE + "/state", key="not-a-uuid"), 400, "invalid_session")
    assert_error(
        send(BASE + "/load", {**load_body(state), "preset": "unknown"}), 400, "invalid_request"
    )
    for payload in (
        {"action": "set_rate", "playback_rate": 0.75},
        {"action": "set_rate", "playback_rate": True},
        {"action": "set_rate", "playback_rate": "0.5"},
        {"action": "set_rate"},
        {"action": "play", "playback_rate": 0.5},
        {"action": "skip"},
    ):
        assert_error(
            send(BASE + "/control", {**payload, "expected": expected}), 400, "invalid_request"
        )
    for raw in (b"{", b"[]", b'{"data":"' + b"x" * MAX_REQUEST_BYTES + b'"}', b"\xff"):
        assert_error(send(BASE + "/control", raw), 400, "invalid_request")
    assert_error(
        send(
            BASE + "/control", {"action": "play", "expected": expected}, content_type="text/plain"
        ),
        400,
        "invalid_request",
    )

    def relocate(target="parcel_01", x=150, at=100, duration=1000):
        return disruption(
            state,
            "relocate_parcel",
            at_ms=at,
            entity_id=target,
            to={"x_cm": x, "y_cm": 350},
            duration_ms=duration,
        )

    rejected = [
        relocate(target="absent"),
        relocate(target="shelf_01"),
        relocate(x=799),
        relocate(x=float("nan")),
        relocate(at=0),
        relocate(at=101),
        relocate(at=29_900),
        relocate(at=4500),
        relocate(duration=110),
        disruption(state, "teleport"),
        disruption(state, "cart_clear", at_ms=10_500),
        disruption(state, "lighting", at_ms=14_000, intensity=1, color_cast="neutral"),
        disruption(state, "lighting", intensity=2, color_cast="neutral"),
        disruption(state, "sensor_availability", capability="gps", available=False),
        disruption(state, "sensor_availability", capability="lidar_scan", available=1),
    ]
    for payload in rejected:
        assert_error(send(BASE + "/disrupt", payload), 400, "invalid_request")
    assert send(BASE + "/state")[1] == state
    assert service.get_session(key).runtime.engine.export_state()["inputs"] == []
    valid = disruption(state, "sensor_availability", capability="lidar_scan", available=False)
    code, accepted, _ = send(BASE + "/disrupt", valid)
    assert code == 200 and accepted["event"]["sequence"] == 1
    conflict = disruption(state, "sensor_availability", capability="lidar_scan", available=True)
    assert_error(send(BASE + "/disrupt", conflict), 400, "invalid_request")
    assert send(BASE + "/state")[1] == accepted["state"]


def test_http_concurrent_retries_and_session_isolation(api):
    send, service, _, key = api
    other = str(uuid4())
    other_run = load(send, key=other)
    initial = load_body(send(BASE + "/state")[1])

    def twice(pool, path, payload):
        first = pool.submit(send, BASE + path, payload)
        second = pool.submit(send, BASE + path, payload)
        return first.result(timeout=4), second.result(timeout=4)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = twice(pool, "/load", initial)
        assert one[0] == two[0] == 200
        run = one[1]
        assert run == two[1] and run["generation"] == 1
        request = disruption(run, "cart_block")
        first, second = twice(pool, "/disrupt", request)
        assert first[0] == second[0] == 200
        assert first[1] == second[1]
    assert len(service.get_session(key).runtime.engine.export_state()["inputs"]) == 1
    changed = {**request, "disruption": {"kind": "cart_clear"}}
    assert_error(send(BASE + "/disrupt", changed), 409, "state_conflict")
    assert_error(
        send(BASE + "/control", {"expected": identity(run), "action": "play"}, key=other),
        409,
        "stale_run",
    )
    assert send(BASE + "/state", key=other)[1] == other_run
    assert_error(
        send(BASE + "/load", {**initial, "expected": identity(run)}), 409, "state_conflict"
    )


def test_http_old_mutations_rejected_while_reset_checkpoint_is_in_flight(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions.sqlite")
    key = str(uuid4())
    service = service_for([0.0], store)
    saving, release = Event(), Event()
    save = store.save

    with http_api(service, tmp_path, key) as send:
        run = load(send)
        delayed_load = load_body(run)
        delayed_event = disruption(run, "cart_block")

        def block_reset(session_id, state, memory):
            saving.set()
            assert release.wait(5)
            save(session_id, state, memory)

        monkeypatch.setattr(store, "save", block_reset)
        with ThreadPoolExecutor(max_workers=1) as pool:
            reset = pool.submit(control, send, run, "reset")
            try:
                assert saving.wait(4)
                replacement = send(BASE + "/state")[1]
                assert replacement["run_id"] != run["run_id"]
                assert_error(control(send, run, "step"), 409, "stale_run")
                assert_error(send(BASE + "/load", delayed_load), 409, "stale_run")
                assert_error(send(BASE + "/disrupt", delayed_event), 409, "stale_run")
                assert send(BASE + "/state")[1] == replacement
                assert not reset.done()
            finally:
                release.set()
            assert reset.result(timeout=4)[1] == replacement
    store.close()


def test_http_state_and_scheduler_remain_live_during_blocked_provider(api, monkeypatch):
    send, service, clock, key = api
    run = load(send)
    assert control(send, run, "play")[0] == 200
    provider = service.get_session(key).agent.provider
    entered, release = Event(), Event()
    original = provider.propose_intent

    def wait_for_release(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "propose_intent", wait_for_release)
    with ThreadPoolExecutor(max_workers=1) as pool:
        answer = pool.submit(send, "/api/ask", {"question": "Where are you?"})
        try:
            assert entered.wait(4)
            clock[0] = 1.0
            assert send(BASE + "/state")[1]["sim_time_ms"] == 0
            service.pump()
            assert send(BASE + "/state")[1]["sim_time_ms"] == 500
            clock[0] = 2.0
            assert send(BASE + "/state")[1]["sim_time_ms"] == 500
        finally:
            release.set()
        assert answer.result(timeout=4)[0] == 200


def test_http_save_and_control_failures_are_typed_and_sanitized(tmp_path, monkeypatch):
    key = str(uuid4())
    store = SessionStore(tmp_path / "sessions.sqlite")
    service = service_for([0.0], store)
    save = store.save

    def fail(*_args):
        raise OSError("private-client-secret")

    with http_api(service, tmp_path, key) as send:
        run = load(send)
        monkeypatch.setattr(store, "save", fail)
        response = control(send, run, "set_rate", playback_rate=1)
        assert_error(response, 503, "runtime_unavailable")
        assert "private-client-secret" not in json.dumps(response[1])
        failed = send(BASE + "/state")[1]
        assert failed["state"] == "error" and failed["persistence_error"]
        assert "private-client-secret" not in json.dumps(failed)
        assert_error(control(send, run, "pause"), 503, "runtime_unavailable")

        monkeypatch.setattr(store, "save", save)
        recovered = control(send, run, "reset")
        assert recovered[0] == 200 and recovered[1]["run_id"] != run["run_id"]
        runtime = service.get_session(key).runtime
        monkeypatch.setattr(runtime, "_ingest", fail)
        response = control(send, recovered[1], "step")
        assert_error(response, 503, "runtime_unavailable")
        state = send(BASE + "/state")[1]
        assert state["state"] == "error" and state["error"] == "Observation ingestion failed."
        assert state["observation"]["latest"]["robot_pose"]["sim_time_ms"] == 0
        assert "private-client-secret" not in json.dumps(state)
    store.close()


def test_http_queued_controls_cannot_commit_after_an_earlier_save_failure(tmp_path, monkeypatch):
    key = str(uuid4())
    store = SessionStore(tmp_path / "sessions.sqlite")
    service = service_for([0.0], store)
    saving, queued, release = Event(), Event(), Event()
    save = store.save
    queue_save = service._queue_save
    writes = []

    def fail_first(session_id, state, memory):
        writes.append(session_id)
        if len(writes) == 1:
            saving.set()
            assert release.wait(5)
            raise OSError("checkpoint write failed")
        save(session_id, state, memory)

    def track_queue(*args, **kwargs):
        future = queue_save(*args, **kwargs)
        if saving.is_set():
            queued.set()
        return future

    with http_api(service, tmp_path, key) as send:
        run = load(send)
        previous_checkpoint = store.load(key)
        monkeypatch.setattr(store, "save", fail_first)
        monkeypatch.setattr(service, "_queue_save", track_queue)
        with ThreadPoolExecutor(max_workers=2) as pool:
            rate = pool.submit(control, send, run, "set_rate", playback_rate=1)
            try:
                assert saving.wait(4)
                step = pool.submit(control, send, run, "step")
                assert queued.wait(4)
            finally:
                release.set()
            assert_error(rate.result(timeout=4), 503, "runtime_unavailable")
            assert_error(step.result(timeout=4), 503, "runtime_unavailable")
        assert writes == [key]
        assert store.load(key) == previous_checkpoint
    store.close()


def test_http_idempotency_survives_checkpoint_restore(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    key = str(uuid4())
    with http_api(service_for([0.0], store), tmp_path, key) as send:
        load_request = load_body(send(BASE + "/state")[1])
        run = send(BASE + "/load", load_request)[1]
        request = disruption(run, "cart_block")
        accepted = send(BASE + "/disrupt", request)[1]
        assert send(BASE + "/state")[1] == accepted["state"]

    restored_service = AgentService(
        ProviderConfig(mode="rule-based", api_key=None), store=store, background=False
    )
    with http_api(restored_service, tmp_path, key) as send:
        restored = send(BASE + "/state")[1]
        assert restored["run_id"] == run["run_id"] and restored["generation"] > run["generation"]
        assert restored["revision"] > accepted["state"]["revision"]
        assert send(BASE + "/load", load_request)[1] == restored
        assert send(BASE + "/disrupt", request)[1] == {
            "event": accepted["event"],
            "state": restored,
        }
        assert_error(control(send, run, "step"), 409, "stale_run")
        assert_error(send(BASE + "/disrupt", disruption(run, "cart_clear")), 409, "stale_run")
        assert control(send, restored, "step")[1]["sim_time_ms"] == 100
        assert len(restored_service.get_session(key).runtime.presenter_requests) == 1
    store.close()


def test_http_export_not_ready(api):
    send, _, _, _ = api
    load(send)
    assert_error(send(BASE + "/export", key=""), 400, "invalid_session")
    code, error, headers = send(BASE + "/export")
    assert_error((code, error), 503, "export_not_ready")
    assert "Content-Disposition" not in headers


def test_http_export_delegation_boundary_attachment_and_validation(tmp_path):
    key = str(uuid4())
    entered, release = Event(), Event()

    class TestExporter:
        target = None
        block = False
        invalid = False

        def export(self, target):
            self.target = target
            session = service.get_session(target.session_id)
            with session.lock:
                observations = session.agent.memory.query_samples(
                    run_id=target.run_id, end_ms=target.sim_time_ms
                )
                changes = list(session.runtime.belief_changes)
            if self.block:
                entered.set()
                assert release.wait(5)
            if self.invalid:
                observations[0] = observations[0].model_copy(update={"run_id": uuid4()})
            return SimulationExportBundle(
                schema_version=1,
                run_id=target.run_id,
                epoch_utc=target.epoch_utc,
                sim_time_ms=target.sim_time_ms,
                snapshot_sequence=target.snapshot_sequence,
                scenario=target.scenario,
                presenter_events=list(target.presenter_events),
                observations=observations,
                belief_changes=changes,
            )

    exporter = TestExporter()
    service = service_for([0.0], exporter=exporter)
    with http_api(service, tmp_path, key) as send:
        assert_error(send(BASE + "/export"), 409, "state_conflict")
        run = load(send)
        accepted = send(BASE + "/disrupt", disruption(run, "cart_block"))[1]
        run = accepted["state"]
        code, bundle, headers = send(BASE + "/export")
        assert code == 200 and bundle["schema_version"] == 1
        assert bundle["run_id"] == run["run_id"]
        assert (
            headers["Content-Disposition"]
            == f'attachment; filename="groundtruth-replay-{run["run_id"]}.json"'
        )
        assert headers["Cache-Control"] == "no-store"
        assert exporter.target.session_id == key
        assert exporter.target.run_id == UUID(run["run_id"])
        assert exporter.target.sim_time_ms == exporter.target.snapshot_sequence == 0
        assert exporter.target.next_sample_sequence == 4
        assert bundle["presenter_events"] == [accepted["event"]]
        assert len(bundle["observations"]) == 4 and bundle["belief_changes"]
        assert {"session_id", "api_key", "memory", "history", "ledger"}.isdisjoint(bundle)
        assert key not in json.dumps(bundle)
        exporter.invalid = True
        assert_error(send(BASE + "/export"), 500, "simulation_failed")
        exporter.invalid = False
        exporter.block = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            export = pool.submit(send, BASE + "/export")
            try:
                assert entered.wait(4)
                assert send(BASE + "/state")[1] == run
                assert control(send, run, "reset")[0] == 200
            finally:
                release.set()
            assert_error(export.result(timeout=4), 409, "stale_run")


def test_repeated_restore_reserves_generation_before_exposure(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    key = str(uuid4())
    first = service_for([0.0], store)
    second = service_for([0.0], store)
    third = service_for([0.0], store)
    try:
        initial = first.read_live_simulation(key)
        first.load_warehouse(
            key,
            SimulationLoadRequest(preset=PRESET, expected=initial, request_id=uuid4()),
        )
        restored = second.read_live_simulation(key)
        # Simulate another process starting from durable storage before the
        # restored process has saved any subsequent control or tick.
        again = third.read_live_simulation(key)
        assert again.run_id == restored.run_id
        assert again.generation > restored.generation
        from src.web.service import StaleRequestError

        with pytest.raises(StaleRequestError):
            third.apply_simulation_control(
                key,
                SimulationControlRequest(action="step", expected=restored),
            )
    finally:
        # Retire without writing old incarnations over the latest checkpoint.
        for service in (first, second, third):
            for session in service._sessions.values():
                session.runtime = None
            service.shutdown()
        store.close()


def test_restore_is_not_exposed_when_incarnation_save_fails(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions.sqlite")
    key = str(uuid4())
    first = service_for([0.0], store)
    second = service_for([0.0], store)
    try:
        initial = first.read_live_simulation(key)
        run = first.load_warehouse(
            key, SimulationLoadRequest(preset=PRESET, expected=initial, request_id=uuid4())
        )

        def fail_save(*_args):
            raise OSError("checkpoint unavailable")

        monkeypatch.setattr(store, "save", fail_save)
        failed = second.read_live_simulation(key)
        assert failed.state == "error"
        assert failed.run_id is None
        assert store.load(key)[0]["simulation_generation"] == run.generation
    finally:
        for service in (first, second):
            for session in service._sessions.values():
                session.runtime = None
            service.shutdown()
        store.close()
