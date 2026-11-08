"""Warehouse observations register only identities the camera has detected."""

from uuid import uuid4

import pytest

from src.contracts import FactQuery
from src.contracts.simulation_api import SimulationEvidenceRequest, SimulationRunIdentity
from src.declarative.memory_repository import MemoryRepository
from src.procedural.intent_provider import IntentProviderUnavailableError
from src.providers import ProviderConfig
from src.sensorimotor.world_models import SimulatedObject
from src.web.service import AgentService, StaleRequestError
from src.web.simulation_presets import warehouse_preset


def test_history_question_uses_complete_grounded_result_when_provider_request_is_too_large(
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

        def reject_large_result(**_kwargs):
            try:
                raise RequestTooLarge("request too large")
            except RequestTooLarge as cause:
                raise IntentProviderUnavailableError("explanation rejected") from cause

        monkeypatch.setattr(
            session.agent.provider,
            "generate_grounded_explanation",
            reject_large_result,
            raising=False,
        )
        response = session.ask("show the history of the parcel 01?")
        assert "Sighting timeline for parcel_01" in response["answer"]
        assert "6500 ms" in response["answer"]
        assert "26500 ms" in response["answer"]
        assert response["react_trace"]["explanation_source"] == "deterministic_fallback"
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
