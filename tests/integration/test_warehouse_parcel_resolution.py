"""Warehouse observations register only identities the camera has detected."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.contracts import FactQuery, ObservationRequest
from src.contracts.simulation_api import SimulationEvidenceRequest, SimulationRunIdentity
from src.declarative.memory_repository import MemoryRepository
from src.procedural.intent_provider import IntentProviderUnavailableError
from src.providers import ProviderConfig
from src.providers.groq_provider import RealGroqClient
from src.sensorimotor.world_models import SimulatedObject
from src.web.service import AgentService, StaleRequestError
from src.web.simulation_presets import warehouse_preset


@pytest.mark.parametrize(
    ("model_reply", "fallback_reason"),
    [
        (None, None),
        (
            "The parcel was seen at the packing station.",
            "The model changed the required evidence wording.",
        ),
        ("", "The model returned no answer text."),
        (RuntimeError("upstream timeout"), "The model request for the final answer failed."),
    ],
)
def test_live_parcel_answer_retains_exact_camera_citation(
    monkeypatch, model_reply, fallback_reason
):
    question = "Where is parcel 01 now?"
    calls = []

    def complete(_client, *, messages, **kwargs):
        calls.append(messages)
        assert messages[1]["content"] == question
        if kwargs.get("tools"):
            tool = SimpleNamespace(
                id="call_parcel",
                function=SimpleNamespace(
                    name="object_location_query",
                    arguments='{"entity_mentions": ["parcel 01"], "mode": "current"}',
                ),
            )
            return {"choices": [{"message": {"tool_calls": [tool]}}]}
        if isinstance(model_reply, RuntimeError):
            raise model_reply
        evidence = json.loads(messages[-1]["content"])
        if model_reply is not None:
            text = model_reply
        elif "verbatim" in messages[0]["content"]:
            text = evidence["deterministic_evaluation_summary"]
        else:
            text = "The parcel was seen at the packing station."
        return {"choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(RealGroqClient, "create_chat_completion", complete)
    service = AgentService(ProviderConfig(mode="live", api_key="test-key"), background=False)
    key = str(uuid4())
    try:
        service.load_simulation(key, warehouse_preset())
        response = service.get_session(key).ask(question)
        assert len(calls) == 2
        trace = response["react_trace"]
        assert trace["model"] == "llama-3.3-70b-versatile"
        assert trace["explanation_source"] == (
            "deterministic_fallback" if fallback_reason else "llm"
        )
        assert trace["fallback_reason"] == fallback_reason
        assert "camera sample ID:" in response["answer"]
    finally:
        service.shutdown()


def test_history_question_retries_with_compact_evidence_when_provider_request_is_too_large(
    monkeypatch,
):
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    try:
        service.load_simulation(key, warehouse_preset())
        session = service.get_session(key)
        for _ in range(300):
            session.runtime.step()

        class RequestTooLarge(Exception):
            status_code = 413

        calls = []

        def explain(**kwargs):
            calls.append(kwargs["tool_result"])
            if len(calls) == 1:
                try:
                    raise RequestTooLarge("request too large")
                except RequestTooLarge as cause:
                    raise IntentProviderUnavailableError("explanation rejected") from cause
            assert "6500 ms" in str(calls[-1])
            assert "26500 ms" in str(calls[-1])
            assert "sample ID" not in str(calls[-1])
            return "Parcel 01 was observed several times, most recently at 26.5 seconds."

        monkeypatch.setattr(
            session.agent.provider, "generate_grounded_explanation", explain, raising=False
        )
        monkeypatch.setattr(
            session.agent.provider, "verify_grounded_explanation", lambda *_: True, raising=False
        )
        response = session.ask("show the history of the parcel 01?")
        assert len(calls) == 2
        assert (
            response["answer"]
            == "Parcel 01 was observed several times, most recently at 26.5 seconds."
        )
        assert response["react_trace"]["explanation_source"] == "llm"

        # An unsupported paraphrase must never expose the raw timeline as an answer.
        monkeypatch.setattr(
            session.agent.provider, "verify_grounded_explanation", lambda *_: False, raising=False
        )
        calls.clear()
        with pytest.raises(IntentProviderUnavailableError, match="verified history"):
            session.ask("show the history of the parcel 01?")
    finally:
        service.shutdown()


def test_run_scoped_evidence_reports_sightings_and_revision_without_hidden_positions():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    try:
        service.load_simulation(key, warehouse_preset())
        session = service.get_session(key)
        identity = SimulationRunIdentity(
            run_id=session.runtime.run_id, generation=session._generation
        )
        request = SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
        hidden = service.read_simulation_evidence(
            key, SimulationEvidenceRequest(expected=identity, entity_id="parcel_03")
        )
        assert all(
            all(
                obj["object_id"] != "parcel_03"
                for obj in sample["result"].get("measurements", {}).get("objects", [])
            )
            for sample in hidden["samples"]
        )
        assert hidden["summary"]["last_observed_position"] is None
        for _ in range(75):
            session.runtime.step()
        snapshot = session.snapshot()
        assert snapshot["history"] == []
        inspection_fact = next(
            fact
            for fact in snapshot["active_facts"]
            if fact["subject"] == "parcel_01" and fact["object"] == "inspection_station"
        )
        assert any(
            change["fact_id"] == inspection_fact["fact_id"] and change["revision_event_id"]
            for change in snapshot["simulation_belief_changes"]
        )
        evidence = service.read_simulation_evidence(key, request)
        assert evidence["summary"]["supported_location"] == "inspection_station"
        assert evidence["events"]
        assert all(item["run_id"] == str(identity.run_id) for item in evidence["samples"])
        assert [s["sim_time_ms"] for s in evidence["samples"]] == sorted(
            s["sim_time_ms"] for s in evidence["samples"]
        )
        for _ in range(225):
            session.runtime.step()
        route = service.read_simulation_evidence(
            key, SimulationEvidenceRequest(expected=identity, entity_id="route_A")
        )
        assert route["summary"]["status"] == "clear"
        assert len(route["events"]) >= 2
        assert all(sample["request"]["capability"] == "lidar_scan" for sample in route["samples"])
        service.load_simulation(key, warehouse_preset())
        with pytest.raises(StaleRequestError):
            service.read_simulation_evidence(key, request)
    finally:
        service.shutdown()


def test_warehouse_camera_detections_can_be_queried():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    try:
        session = service.get_session(key)
        assert any(f.subject == "building_1" for f in session.agent.memory.query_facts(FactQuery()))
        service.load_simulation(key, warehouse_preset())
        state = session.snapshot()
        assert not any(f["subject"] == "building_1" for f in state["active_facts"])
        assert "building_1" not in state["graph"]["nodes"]
        assert session.agent.memory.resolve_entity("box_01").status == "missing"
        with MemoryRepository(":memory:") as unscoped:
            unscoped.restore_snapshot(session.agent.memory.export_snapshot())
            assert any(f.subject == "building_1" for f in unscoped.query_facts(FactQuery()))
        assert state["environment_source"] == "warehouse_truth"
        assert {"parcel_01", "parcel_02", "parcel_03", "shelf_01", "shelf_02", "cart_01"} <= set(
            state["environment"]["objects"]
        )
        assert {"shelf_01", "shelf_02", "cart_01"} <= set(state["environment"]["obstacles"])
        assert "parcel_01" not in state["environment"]["obstacles"]
        assert state["environment"]["robot"]["location"] == "warehouse"
        assert session.agent.memory.resolve_entity("parcel_01").canonical_entity_id == "parcel_01"
        assert session.agent.memory.resolve_entity("parcel_03").canonical_entity_id == "parcel_03"
        assert "No" in session.ask("Where is parcel 03 now?")["answer"]
        assert session.agent.memory.resolve_entity("shelf_01").canonical_entity_id == "shelf_01"
        shelf_answer = session.ask("Where is shelf 01 now?")["answer"]
        assert "shelf_01 was observed at x=300 cm, y=300 cm" in shelf_answer
        assert (
            "was observed at x=300 cm, y=300 cm" in session.ask("Where is shelf 1 now?")["answer"]
        )
        assert session.agent.memory.resolve_entity("cart_01").canonical_entity_id == "cart_01"
        assert "No" in session.ask("Where is cart 01 now?")["answer"]
        answer = session.ask("Where is parcel 01 now?")["answer"]
        assert "parcel_01 was observed at x=80 cm, y=350 cm" in answer
        for _ in range(75):
            session.runtime.step()
        answer = session.ask("Where is parcel 01 now?")["answer"]
        assert "parcel_01 was observed at x=150 cm, y=390 cm" in answer
        answer = session.ask("Where was parcel 01 before, and what changed?")["answer"]
        assert "Observed transition for parcel_01" in answer
        assert "inspection_station" in answer and "packing_station" in answer
        state = session.snapshot()["environment"]
        assert state["objects"]["parcel_01"]["x_cm"] == 150
        assert state["obstacles"]["cart_01"]["x_cm"] == 180
        for _ in range(35):
            session.runtime.step()
        assert session.snapshot()["environment"]["obstacles"]["cart_01"]["x_cm"] == 100
        first_run = session.runtime.run_id
        # Simulate a checkpoint written before observed identities were registered.
        session.agent.memory._conn.execute(
            "DELETE FROM aliases WHERE canonical_entity_id = 'parcel_01'"
        )
        session.agent.memory._conn.commit()
        saved, image = session.persisted_state(), session.agent.memory.export_snapshot()
        session.restore(saved, image)
        assert not any(f["subject"] == "building_1" for f in session.snapshot()["active_facts"])
        assert session.agent.memory.resolve_entity("parcel_01").canonical_entity_id == "parcel_01"
        assert "Last confirmed sighting" in session.ask("Where is parcel 01 now?")["answer"]
        service.load_simulation(key, warehouse_preset())
        assert session.runtime.run_id != first_run
        assert all(
            f["context"]["extra_context"].get("run_id") == str(session.runtime.run_id)
            for f in session.snapshot()["active_facts"]
        )
    finally:
        service.shutdown()


def test_visible_cart_resolves_from_camera_evidence():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    scenario = warehouse_preset().model_copy(deep=True)
    cart = next(entity for entity in scenario.entities if entity.entity_id == "cart_01")
    cart.footprint.x_cm, cart.footprint.y_cm = 100, 300
    try:
        service.load_simulation(key, scenario)
        session = service.get_session(key)
        answer = session.ask("Where is cart 01 now?")["answer"]
        assert "cart_01 was observed at x=100 cm, y=300 cm" in answer
    finally:
        service.shutdown()


def test_new_scenario_types_and_names_need_no_hard_coded_registration():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    scenario = warehouse_preset().model_copy(deep=True)
    cart = next(entity for entity in scenario.entities if entity.entity_id == "cart_01")
    cart.entity_id, cart.kind = "forklift_07", "forklift"
    cart.footprint.x_cm, cart.footprint.y_cm = 100, 300
    for event in scenario.events:
        if getattr(event, "entity_id", None) == "cart_01":
            event.entity_id = "forklift_07"
    try:
        service.load_simulation(key, scenario)
        session = service.get_session(key)
        assert "forklift_07" in session.snapshot()["environment"]["objects"]
        answer = session.ask("Where is forklift 07 now?")["answer"]
        assert "was observed at x=100 cm, y=300 cm" in answer
        assert (
            "was observed at x=100 cm, y=300 cm" in session.ask("Where is the forklift?")["answer"]
        )
        saved, image = session.persisted_state(), session.agent.memory.export_snapshot()
        session.restore(saved, image)
        assert (
            "was observed at x=100 cm, y=300 cm" in session.ask("Where is forklift 07?")["answer"]
        )
    finally:
        service.shutdown()


def test_mock_environment_registers_new_object_names_before_perception():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    session = service.get_session(str(uuid4()))
    try:
        session.agent.environment.configure_object_for_test(
            SimulatedObject(
                object_id="forklift_07",
                x_cm=30,
                y_cm=30,
                location="room_101",
                intrinsic_color="blue",
            )
        )
        answer = session.ask("What color is forklift 07?")["answer"]
        assert "Blue" in answer
        answer = session.ask("Where is forklift 07 now?")["answer"]
        assert "was observed at x=30 cm, y=30 cm in room_101" in answer
        session.agent.environment.configure_object_for_test(
            SimulatedObject(
                object_id="forklift_07",
                x_cm=50,
                y_cm=40,
                location="room_101",
                intrinsic_color="blue",
            )
        )
        answer = session.ask("Where is forklift 07 now?")["answer"]
        assert "was observed at x=50 cm, y=40 cm in room_101" in answer
        session.agent.environment.configure_object_for_test(
            SimulatedObject(
                object_id="forklift_07",
                x_cm=60,
                y_cm=40,
                location="room_101",
                intrinsic_color="blue",
                visible=False,
            )
        )
        answer = session.ask("Where is forklift 07 now?")["answer"]
        assert "x=60 cm" not in answer
    finally:
        service.shutdown()


def test_station_nodes_and_front_route_audit_follow_current_heading():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    try:
        service.load_simulation(key, warehouse_preset())
        session = service.get_session(key)
        for _ in range(300):
            session.runtime.step()
        snapshot = session.snapshot()
        assert {"warehouse", "packing_station", "inspection_station"} <= set(
            snapshot["graph"]["nodes"]
        )
        assert any(
            fact["subject"] == "packing_station"
            and fact["object"] == "warehouse"
            and fact["source_agent"] == "scenario_map"
            for fact in snapshot["active_facts"]
        )
        assert session.runtime.read().robot_pose.direction == "east"
        result = session.ask("Why did the front route belief change?")
        assert all(
            fact["subject"] == "route_B"
            for trail in result["audit_trails"]
            for fact in trail["facts"]
        )
        assert result["audit_trails"]
        result = session.ask("Is the front route clear now?")
        assert "route_B" in result["answer"] and "blocked" in result["answer"]
        named_current = session.ask("Is route A clear now?")
        assert "not aligned" in named_current["answer"]
        named = session.ask("Why did route A change?")
        assert any(
            fact["subject"] == "route_A"
            for trail in named["audit_trails"]
            for fact in trail["facts"]
        )
    finally:
        service.shutdown()


def test_route_and_station_evidence_use_the_loaded_scenario_map():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    scenario = warehouse_preset().model_copy(deep=True)
    station = next(entity for entity in scenario.entities if entity.entity_id == "packing_station")
    station.entity_id = "dispatch_zone"
    scenario.routes[0].route_id = "aisle_01"
    try:
        service.load_simulation(key, scenario)
        session = service.get_session(key)
        assert session.runtime.current_route_id() == "aisle_01"
        facts = session.agent.memory.query_facts(
            FactQuery(subject="parcel_01", predicate="located_at")
        )
        assert {fact.object for fact in facts} == {"dispatch_zone"}
        response = session.ask("Is the front route clear?")
        assert "aisle_01" in response["answer"]
        assert "packing_station" not in session.snapshot()["graph"]["nodes"]
    finally:
        service.shutdown()


def test_off_station_parcel_verification_bug_at_25_5_seconds():
    """Reproduce default preset at 25.5s: off-station sample cannot verify old station."""
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    try:
        service.load_simulation(key, warehouse_preset())
        session = service.get_session(key)
        # Step to 25.5 simulated seconds (255 steps * 100ms)
        for _ in range(255):
            session.runtime.step()
        assert session.runtime.sim_time_ms == 25500

        identity = SimulationRunIdentity(
            run_id=session.runtime.run_id, generation=session._generation
        )
        request = SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
        evidence = service.read_simulation_evidence(key, request)

        summary = evidence["summary"]
        # Verified off-station coordinates with null station
        assert summary["verification_status"] == "verified"
        assert summary["supported_location"] is None
        assert summary["current_location"] is None
        assert summary["last_observed_position"] == {"x_cm": 372.5, "y_cm": 367.5}
        assert summary["earlier_station"] == "inspection_station"

        # SQLite fact retains earlier station in history without mutation
        active_facts = session.agent.memory.query_facts(
            FactQuery(subject="parcel_01", predicate="located_at", active_only=True)
        )
        assert len(active_facts) == 1
        assert active_facts[0].object == "inspection_station"
    finally:
        service.shutdown()


def test_full_acceptance_station_a_to_aisle_to_station_b_sqlite_periodic_sensing():
    """Full acceptance from #48: A -> aisle -> B with periodic camera acquisition and SQLite.

    Asserts:
    1. Chat history remains empty throughout (no chat questions asked).
    2. At Station A (5.0s): initial fact packing_station in SQLite.
    3. In aisle (6.0s): independently retrievable aisle sample in SQLite ledger;
       leaves the SQLite station fact chain intact (packing_station); 0 revisions.
    4. At Station B (7.0s): B sample produces exactly 1 atomic A-to-B revision in SQLite audit.
    """
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    try:
        service.load_simulation(key, warehouse_preset())
        session = service.get_session(key)
        memory = session.agent.memory

        # Ensure empty chat history
        assert len(session.history) == 0

        # Step to 5.0 simulated seconds (50 steps * 100ms): Station A (packing_station)
        for _ in range(50):
            session.runtime.step()
        assert session.runtime.sim_time_ms == 5000

        identity = SimulationRunIdentity(
            run_id=session.runtime.run_id, generation=session._generation
        )
        ev_a = service.read_simulation_evidence(
            key, SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
        )
        assert ev_a["summary"]["verification_status"] == "verified"
        assert ev_a["summary"]["current_location"] == "packing_station"

        # Check SQLite facts at Station A
        facts_a = memory.query_facts(
            FactQuery(subject="parcel_01", predicate="located_at", active_only=True)
        )
        assert len(facts_a) == 1
        assert facts_a[0].object == "packing_station"
        trail_a = memory.get_audit_chain(facts_a[0].fact_id)
        assert len(trail_a.events) == 0
        assert len(ev_a["events"]) == 0

        # Step to 6.0 simulated seconds (10 steps): Corridor/Aisle at (115, 370)
        for _ in range(10):
            session.runtime.step()
        assert session.runtime.sim_time_ms == 6000

        ev_aisle = service.read_simulation_evidence(
            key, SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
        )
        assert ev_aisle["summary"]["verification_status"] == "verified"
        assert ev_aisle["summary"]["current_location"] is None
        assert ev_aisle["summary"]["earlier_station"] == "packing_station"
        assert ev_aisle["summary"]["last_observed_position"] == {"x_cm": 115.0, "y_cm": 370.0}

        # Assert independently retrievable aisle sample in SQLite observation ledger
        aisle_sighting = memory.get_last_sighting(
            run_id=session.runtime.run_id,
            entity_id="parcel_01",
            capability="camera_detect",
            at_sim_time_ms=6000,
        )
        assert aisle_sighting is not None
        assert aisle_sighting.sim_time_ms == 6000

        # Assert station chain in SQLite is left intact (no mutation, still packing_station)
        facts_aisle = memory.query_facts(
            FactQuery(subject="parcel_01", predicate="located_at", active_only=True)
        )
        assert len(facts_aisle) == 1
        assert facts_aisle[0].object == "packing_station"
        assert facts_aisle[0].fact_id == facts_a[0].fact_id
        trail_aisle = memory.get_audit_chain(facts_aisle[0].fact_id)
        assert len(trail_aisle.events) == 0
        assert len(ev_aisle["events"]) == 0

        # Step to 7.0 simulated seconds (10 steps): Station B (inspection_station)
        for _ in range(10):
            session.runtime.step()
        assert session.runtime.sim_time_ms == 7000

        ev_b = service.read_simulation_evidence(
            key, SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
        )
        assert ev_b["summary"]["verification_status"] == "verified"
        assert ev_b["summary"]["current_location"] == "inspection_station"

        # Assert SQLite active fact is now Station B
        facts_b = memory.query_facts(
            FactQuery(subject="parcel_01", predicate="located_at", active_only=True)
        )
        assert len(facts_b) == 1
        assert facts_b[0].object == "inspection_station"

        # Assert exactly ONE revision was produced from Station A to Station B
        trail_b = memory.get_audit_chain(facts_b[0].fact_id)
        assert len(trail_b.events) == 1
        assert trail_b.events[0].input_fact_ids == [facts_a[0].fact_id, facts_b[0].fact_id]
        assert trail_b.events[0].output_fact_ids == [facts_b[0].fact_id]
        assert "revising previous location packing_station" in trail_b.events[0].reason
        assert len(ev_b["events"]) == 1
        assert ev_b["events"][0]["policy_rule"] == "camera_observation_revision"
        assert "revising previous location packing_station" in ev_b["events"][0]["reason"]

        # Verify chat history remained completely empty throughout
        assert len(session.history) == 0
    finally:
        service.shutdown()


@pytest.fixture
def loaded_warehouse():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    key = str(uuid4())
    try:
        service.load_simulation(key, warehouse_preset())
        session = service.get_session(key)
        runtime = session.runtime
        assert runtime is not None
        identity = SimulationRunIdentity(run_id=runtime.run_id, generation=session._generation)
        yield service, key, session, runtime, identity
    finally:
        service.shutdown()


def _step_to(runtime, target_ms):
    while runtime.sim_time_ms < target_ms:
        runtime.step()
    assert runtime.sim_time_ms == target_ms


def test_current_answer_evidence_and_graph_share_the_paused_parcel_assessment(loaded_warehouse):
    service, key, session, runtime, identity = loaded_warehouse
    request = SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
    checkpoints = [
        (5000, "verified", "packing_station", "packing_station"),
        (5100, "verified", "packing_station", "packing_station"),
        (6000, "earlier_station", "packing_station", None),
        (7000, "verified", "inspection_station", "inspection_station"),
        (24500, "last_known", "inspection_station", None),
        (25500, "earlier_station", "inspection_station", None),
    ]
    for time_ms, edge_status, stored_station, current_station in checkpoints:
        _step_to(runtime, time_ms)
        if time_ms == 5100:
            # A newer targeted scan for another entity must not replace the applicable attempt.
            runtime.observe(ObservationRequest(capability="camera_detect", target="parcel_03"))

        evidence = service.read_simulation_evidence(key, request)
        assessment = evidence["location_assessment"]
        response = session.ask("Where is parcel_01 now?", expected=identity)
        snapshot = session.snapshot()
        edge = next(
            edge
            for edge in snapshot["graph"]["edges"]
            if edge["source"] == "parcel_01" and edge["predicate"] == "located_at"
        )
        assert edge["target"] == stored_station
        assert edge["location_evidence"] == {"status": edge_status, "assessment": assessment}
        assert response["simulation_evidence"]["location_assessment"] == assessment
        assert (
            snapshot["simulation_status"]["sim_time_ms"]
            == assessment["as_of_sim_time_ms"]
            == time_ms
        )
        assert assessment["run_id"] == str(runtime.run_id)
        assert (
            evidence["summary"]["current_location"]
            == assessment["current_location"]
            == current_station
        )
        assert evidence["summary"]["verification_status"] == assessment["verification_status"]
        assert str(assessment["last_sighting"]["sample_id"]) in response["answer"]
        if time_ms == 5100:
            assert evidence["summary"]["latest_sensor_attempt"]["sim_time_ms"] == 5000
        if edge_status == "last_known":
            assert assessment["freshness"] == "stale"
            assert response["status"] == "uncertain"
            assert "Last confirmed sighting" in response["answer"]
            last_seen = session.ask("When was parcel_01 last seen?", expected=identity)
            assert last_seen["simulation_evidence"]["location_assessment"] == assessment
        else:
            assert response["status"] == "verified"
            if current_station is None:
                assert "Earlier recorded station" in response["answer"]
                assert stored_station not in response["answer"].split("Earlier recorded station")[0]


def test_historical_cutoff_and_change_answer_cite_distinct_camera_evidence(loaded_warehouse):
    service, key, session, runtime, identity = loaded_warehouse
    _step_to(runtime, 7500)
    historical = session.ask("Where was parcel_01 at 6.7 seconds?", expected=identity)
    assert historical["status"] == "verified"
    assert "6700 ms" in historical["answer"] and "captured at 6500 ms" in historical["answer"]
    assert [sample["sim_time_ms"] for sample in historical["sensor_telemetry"]] == [6500]
    assert "interpolated" in historical["answer"]
    assert not any(
        operation["name"] == "temporal_query_latest"
        for operation in historical["react_trace"]["operations"]
    )

    change = session.ask(
        "What evidence caused parcel_01's location belief to change?", expected=identity
    )
    evidence = service.read_simulation_evidence(
        key, SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
    )
    assert change["status"] == "verified"
    assert change["audit_events"] == evidence["events"]
    (revision,) = change["audit_events"]
    (qualifying_sample,) = change["sensor_telemetry"]
    successor = next(
        fact for fact in evidence["facts"] if fact["fact_id"] in revision["output_fact_ids"]
    )
    assert qualifying_sample["sample_id"] == successor["evidence"]["sample_id"]
    assert qualifying_sample["sim_time_ms"] == successor["evidence"]["sim_time_ms"]
    for expected in (
        "prior location belief was packing_station",
        "inspection_station",
        revision["event_id"],
        revision["policy_rule"],
        revision["reason"],
        qualifying_sample["sample_id"],
        "do not establish the route, exact time of the move, or cause",
    ):
        assert expected in change["answer"]


def test_stop_answer_preserves_original_decision_after_resumption(loaded_warehouse):
    service, key, session, runtime, identity = loaded_warehouse
    question = "Why did the robot stop?"
    missing = session.ask(question, expected=identity)
    assert missing["status"] == "no_data"
    assert "No patrol stop was recorded" in missing["answer"]
    assert missing["sensor_telemetry"] == []

    _step_to(runtime, 12000)
    stopped = session.ask(question, expected=identity)
    stop = stopped["simulation_evidence"]["patrol_stop"]
    assert stop["state"] == "blocked"
    assert stop["sample_id"] in stopped["answer"]
    assert stop["policy_reason"] in stopped["answer"]
    assert str(stop["sensor_sim_time_ms"]) + " ms" in stopped["answer"]
    cited = service.read_simulation_evidence(
        key, SimulationEvidenceRequest(expected=identity, sample_id=stop["sample_id"])
    )["sample"]
    assert cited["request"]["capability"] == "lidar_scan"

    _step_to(runtime, 22000)
    assert runtime.current_decision_summary.state in ("moving", "turning")
    assert runtime.current_decision_summary.sample_id != stop["sample_id"]
    before = session.agent.memory.export_snapshot()
    resumed = session.ask(question, expected=identity)
    assert resumed["simulation_evidence"]["patrol_stop"] == stop
    assert resumed["answer"] == stopped["answer"]
    assert [sample["sample_id"] for sample in resumed["sensor_telemetry"]] == [stop["sample_id"]]
    assert resumed["revisions"] == resumed["audit_events"] == []
    assert session.agent.memory.export_snapshot() == before


def test_captured_assessment_retains_original_as_of_time(loaded_warehouse, monkeypatch):
    service, key, session, runtime, identity = loaded_warehouse
    request = SimulationEvidenceRequest(expected=identity, entity_id="parcel_01")
    _step_to(runtime, 5000)
    captured = service.read_simulation_evidence(key, request)["location_assessment"]

    def explain(**kwargs):
        _step_to(runtime, 6000)
        return kwargs["tool_result"]["deterministic_evaluation_summary"]

    monkeypatch.setattr(
        session.agent.provider, "generate_grounded_explanation", explain, raising=False
    )
    answer = session.ask("Where is parcel_01 now?", expected=identity)
    assert answer["simulation_evidence"]["sim_time_ms"] == 5000
    assert answer["simulation_evidence"]["location_assessment"] == captured
    assert captured["current_location"] == "packing_station"
    current = service.read_simulation_evidence(key, request)["location_assessment"]
    assert current["as_of_sim_time_ms"] == 6000 and current["current_location"] is None
