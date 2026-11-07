"""Session scheduling and persistence with deterministic wall time and blocked providers."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from src.contracts.simulation import ScenarioDefinition, SimulationControl
from src.providers import ProviderConfig
from src.web.service import AgentService, StaleRequestError
from src.web.session_store import SessionStore


@pytest.fixture
def warehouse():
    fixture = Path(__file__).parents[1] / "fixtures/phase_two/contracts.json"
    return ScenarioDefinition.model_validate(json.loads(fixture.read_text())["valid"]["scenario"])


@pytest.fixture
def clock():
    return [0.0]


def service_for(clock, store=None):
    return AgentService(
        ProviderConfig(mode="rule-based", api_key=None),
        store,
        now=lambda: clock[0],
        background=False,
    )


def load(service, key, warehouse):
    service.load_simulation(key, warehouse, epoch_utc=datetime(2026, 1, 1, tzinfo=UTC))
    service.control_simulation(key, SimulationControl(action="play"))


@pytest.mark.parametrize("phase", ["interpretation", "explanation", "review"])
def test_blocked_provider_leaves_simulation_live(clock, warehouse, phase, monkeypatch):
    service = service_for(clock)
    key = str(uuid4())
    load(service, key, warehouse)
    session = service.get_session(key)
    provider = session.agent.provider
    entered, release = Event(), Event()
    original = provider.propose_intent

    def block():
        entered.set()
        assert release.wait(5)

    def interpret(*args, **kwargs):
        block()
        return original(*args, **kwargs)

    def explain(**_kwargs):
        if phase == "explanation":
            block()
        return "Paraphrased."

    def review(*_args):
        block()
        return True

    if phase == "interpretation":
        monkeypatch.setattr(provider, "propose_intent", interpret)
    else:
        monkeypatch.setattr(provider, "generate_grounded_explanation", explain, raising=False)
        if phase == "review":
            monkeypatch.setattr(provider, "verify_grounded_explanation", review, raising=False)
    try:
        with ThreadPoolExecutor() as pool:
            request = pool.submit(session.ask, "Where are you?")
            try:
                assert entered.wait(5)
                clock[0] = 1
                service.pump()
                assert service.read_simulation(key)["status"]["sim_time_ms"] == 500
                service.control_simulation(key, SimulationControl(action="pause"))
            finally:
                release.set()
            item = request.result(timeout=5)
        assert item["simulation_evidence"]["sim_time_ms"] == (
            500 if phase == "interpretation" else 0
        )
        if phase != "interpretation":
            telemetry = item["sensor_telemetry"]
            assert telemetry
            assert telemetry[0]["measurements"]["y_cm"] == warehouse.robot_pose.y_cm
    finally:
        service.shutdown()


@pytest.mark.parametrize("change", ["reset", "replace", "new_chat", "delete", "shutdown"])
def test_inflight_answer_cannot_publish_after_lifecycle_change(
    clock, warehouse, monkeypatch, change
):
    service = service_for(clock)
    key = str(uuid4())
    load(service, key, warehouse)
    session = service.get_session(key)
    entered, release = Event(), Event()

    def explain(**_kwargs):
        entered.set()
        assert release.wait(5)
        return "Late result."

    monkeypatch.setattr(
        session.agent.provider, "generate_grounded_explanation", explain, raising=False
    )
    try:
        with ThreadPoolExecutor() as pool:
            request = pool.submit(session.ask, "Where are you?")
            try:
                assert entered.wait(5)
                if change == "delete":
                    service.delete_session(key)
                    assert service.get_session(key) is not session
                elif change == "replace":
                    service.load_simulation(key, warehouse)
                elif change == "shutdown":
                    service.shutdown()
                else:
                    with session.lock:
                        getattr(session, change)()
            finally:
                release.set()
            with pytest.raises(StaleRequestError):
                request.result(timeout=5)
        assert session.history == []
    finally:
        service.shutdown()


def test_checkpoint_restores_paused_without_reusing_sample_sequence(clock, warehouse, tmp_path):
    path = tmp_path / "sessions.sqlite"
    key = str(uuid4())
    with_store = SessionStore(path)
    service = service_for(clock, with_store)
    load(service, key, warehouse)
    clock[0] = 1
    service.pump()
    service.control_simulation(key, SimulationControl(action="set_rate", playback_rate=0.25))
    session = service.get_session(key)
    before = session.runtime.checkpoint()
    service.shutdown()
    with_store.close()

    clock[0] = 100
    with_store = SessionStore(path)
    restored = service_for(clock, with_store)
    try:
        status = restored.read_simulation(key)["status"]
        assert status["state"] == "paused"
        assert status["playback_rate"] == 0.25
        assert status["sim_time_ms"] == 500
        restored.pump()
        restored.control_simulation(key, SimulationControl(action="step"))
        session = restored.get_session(key)
        after = session.runtime.checkpoint()
        assert after["pose"]["sim_time_ms"] == 600
        assert after["pose"]["sample_sequence"] > before["next_sequence"] - 1
        assert after["pose"]["run_id"] == before["pose"]["run_id"]
    finally:
        restored.shutdown()
        with_store.close()


def test_inactivity_uses_wall_clock_and_isolated_sessions(clock, warehouse):
    service = service_for(clock)
    first, second = str(uuid4()), str(uuid4())
    try:
        load(service, first, warehouse)
        load(service, second, warehouse)
        clock[0] = 59
        service.read_simulation(second)
        service.pump()
        clock[0] = 60
        service.pump()
        assert service.read_simulation(first)["status"]["state"] == "paused"
        assert service.read_simulation(second)["status"]["state"] == "running"
    finally:
        service.shutdown()


def test_slow_checkpoint_does_not_block_other_sessions_or_resurrect_deleted_id(
    clock,
    warehouse,
    tmp_path,
    monkeypatch,
):
    store = SessionStore(tmp_path / "ordered.sqlite")
    service = service_for(clock, store)
    first, second = str(uuid4()), str(uuid4())
    load(service, first, warehouse)
    load(service, second, warehouse)
    old = service.get_session(first)
    real_save = store.save
    entered, release = Event(), Event()

    def delayed_save(key, state, memory):
        if key == first:
            entered.set()
            assert release.wait(5)
        real_save(key, state, memory)

    monkeypatch.setattr(store, "save", delayed_save)
    try:
        clock[0] = 1
        service.pump()
        assert entered.wait(5)
        clock[0] = 2
        service.pump()
        assert service.read_simulation(second)["status"]["sim_time_ms"] == 1000
        with ThreadPoolExecutor() as pool:
            delete = pool.submit(service.delete_session, first)
            release.set()
            delete.result(timeout=5)
        assert store.load(first) is None
        with pytest.raises(StaleRequestError):
            service.save_session(first, old)
        replacement = service.get_session(first)
        assert replacement is not old
        assert "simulation_runtime" not in store.load(first)[0]
    finally:
        release.set()
        service.shutdown()
        store.close()


def test_scheduler_skips_locked_session_and_advances_other(clock, warehouse):
    service = service_for(clock)
    first, second = str(uuid4()), str(uuid4())
    load(service, first, warehouse)
    load(service, second, warehouse)
    session = service.get_session(first)
    entered, release = Event(), Event()

    def hold_lock():
        with session.lock:
            entered.set()
            assert release.wait(5)

    try:
        with ThreadPoolExecutor() as pool:
            holder = pool.submit(hold_lock)
            assert entered.wait(5)
            clock[0] = 1
            pump = pool.submit(service.pump)
            try:
                pump.result(timeout=2)
                assert service.read_simulation(second)["status"]["sim_time_ms"] == 500
            finally:
                release.set()
                holder.result(timeout=5)
        service.pump()
        assert service.read_simulation(first)["status"]["sim_time_ms"] == 500
    finally:
        release.set()
        service.shutdown()


def test_bad_checkpoint_is_preserved_until_explicit_recovery(clock, warehouse, tmp_path):
    store = SessionStore(tmp_path / "corrupt.sqlite")
    key = str(uuid4())
    service = service_for(clock, store)
    load(service, key, warehouse)
    service.shutdown()
    state, image = store.load(key)
    state["simulation_runtime"]["engine"]["snapshot"]["config"]["duration_ms"] += 100
    store.save(key, state, image)
    service = service_for(clock, store)
    try:
        assert service.read_simulation(key)["status"]["state"] == "error"
        service.pump()
        session = service.get_session(key)
        service.save_session(key, session)
        assert store.load(key) == (state, image)
        service.load_simulation(key, warehouse)
        assert service.read_simulation(key)["status"]["state"] == "paused"
    finally:
        service.shutdown()
        store.close()


def test_persistence_failure_pauses_only_affected_session(clock, warehouse, tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "failure.sqlite")
    service = service_for(clock, store)
    first, second = str(uuid4()), str(uuid4())
    load(service, first, warehouse)
    load(service, second, warehouse)
    original = store.save

    def broken(key, state, memory):
        if key == first:
            raise OSError("storage failure")
        original(key, state, memory)

    monkeypatch.setattr(store, "save", broken)
    try:
        clock[0] = 1
        service.pump()
        with pytest.raises(OSError):
            service.get_session(first)._pending_save.result(timeout=5)
        status = service.read_simulation(first)["status"]
        assert status["state"] == "error"
        assert "checkpoint" in status["error"]
        clock[0] = 2
        service.pump()
        assert service.read_simulation(first)["status"]["sim_time_ms"] == 500
        assert service.read_simulation(second)["status"]["sim_time_ms"] == 1000
    finally:
        service.shutdown()
        store.close()


def test_asks_serialize_provider_trace(clock, warehouse, monkeypatch):
    service = service_for(clock)
    key = str(uuid4())
    load(service, key, warehouse)
    session = service.get_session(key)
    provider = session.agent.provider
    original = provider.propose_intent
    entered, release = Event(), Event()
    seen = []

    def propose(question, *args, **kwargs):
        seen.append(question)
        provider.last_tool_call_id = question
        if len(seen) == 1:
            entered.set()
            assert release.wait(5)
        return original(question, *args, **kwargs)

    monkeypatch.setattr(provider, "propose_intent", propose)
    try:
        with ThreadPoolExecutor() as pool:
            first = pool.submit(session.ask, "Where are you?")
            try:
                assert entered.wait(5)
                second = pool.submit(session.ask, "What is your current position?")
                clock[0] = 1
                service.pump()
                assert seen == ["Where are you?"]
            finally:
                release.set()
            assert first.result(timeout=5)["react_trace"]["tool_call_id"] == "Where are you?"
            assert (
                second.result(timeout=5)["react_trace"]["tool_call_id"]
                == "What is your current position?"
            )
    finally:
        service.shutdown()


def test_warehouse_run_tracks_cart_and_camera_over_full_duration(clock, warehouse):
    from src.contracts.models import ObservationUnavailable, SensorObservation
    from src.contracts.simulation import SensorAvailabilityEvent

    warehouse.events.extend(
        SensorAvailabilityEvent(
            kind="sensor_availability",
            at_ms=at_ms,
            sequence=0,
            capability="camera_detect",
            available=available,
        )
        for at_ms, available in [(28_000, False), (29_000, True)]
    )
    service = service_for(clock)
    key = str(uuid4())
    try:
        load(service, key, warehouse)
        session = service.get_session(key)
        run_id = session.runtime.read().status.run_id
        states = {}
        for second in range(1, 31):
            clock[0] = 2 * second
            service.pump()
            states[second] = service.read_simulation(key)["status"]
        assert states[12]["patrol_state"] == "blocked"
        assert states[22]["patrol_state"] == "moving"
        assert states[30]["state"] == "completed"
        scans = {
            sample.sim_time_ms: sample.result
            for sample in session.agent.memory.query_samples(run_id, capability="camera_detect")
        }
        assert list(scans) == list(range(0, 30_001, 500))
        assert "parcel_01" in {item["object_id"] for item in scans[0].measurements["objects"]}
        assert not scans[22_000].measurements["objects"]
        assert "parcel_01" in {item["object_id"] for item in scans[26_000].measurements["objects"]}
        assert isinstance(scans[28_000], ObservationUnavailable)
        assert isinstance(scans[29_000], SensorObservation)
        clock[0] = 62
        service.pump()
        assert service.read_simulation(key)["status"]["sim_time_ms"] == 30_000
    finally:
        service.shutdown()


def test_pending_engine_command_is_reconciled_once_on_restore(clock, warehouse, tmp_path):
    store = SessionStore(tmp_path / "pending.sqlite")
    key = str(uuid4())
    service = service_for(clock, store)
    service.load_simulation(key, warehouse)
    runtime = service.get_session(key).runtime
    decision = runtime.controller.decide(0, runtime._pose, runtime._lidar)
    runtime.engine.command_robot(decision.command)
    service.shutdown()
    service = service_for(clock, store)
    try:
        session = service.get_session(key)
        assert session.runtime.engine.pending_robot_command == decision.command
        service.control_simulation(key, SimulationControl(action="step"))
        assert session.runtime.read().robot_pose == decision.command
        assert session.runtime.engine.pending_robot_command is None
        assert session.runtime.read().status.sim_time_ms == 100
    finally:
        service.shutdown()
        store.close()


def test_managed_scheduler_is_single_and_joins_on_shutdown(clock, warehouse, monkeypatch):
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), now=lambda: clock[0])
    key = str(uuid4())
    session = service.get_session(key)
    assert service._runner is None
    progressed = Event()
    real_pump = service.pump

    def pump():
        real_pump()
        with session.lock:
            if session.runtime and session.runtime.read().status.sim_time_ms >= 500:
                progressed.set()

    monkeypatch.setattr(service, "pump", pump)
    try:
        load(service, key, warehouse)
        worker = service._runner
        service.control_simulation(key, SimulationControl(action="play"))
        assert service._runner is worker
        clock[0] = 1
        assert progressed.wait(5)
    finally:
        service.shutdown()
    assert not worker.is_alive()
