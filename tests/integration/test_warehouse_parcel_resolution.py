"""Warehouse observations register only identities the camera has detected."""

from uuid import uuid4

from src.contracts import FactQuery
from src.declarative.memory_repository import MemoryRepository
from src.providers import ProviderConfig
from src.web.service import AgentService
from src.web.simulation_presets import warehouse_preset


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
        assert session.agent.memory.resolve_entity("parcel_03").status == "missing"
        assert session.agent.memory.resolve_entity("shelf_01").canonical_entity_id == "shelf_01"
        shelf_answer = session.ask("Where is shelf 01 now?")["answer"]
        assert "shelf_01 was observed at x=300 cm, y=300 cm" in shelf_answer
        assert (
            "was observed at x=300 cm, y=300 cm" in session.ask("Where is shelf 1 now?")["answer"]
        )
        assert session.agent.memory.resolve_entity("cart_01").status == "missing"
        answer = session.ask("Where is parcel 01 now?")["answer"]
        assert "parcel_01 was observed at x=80 cm, y=350 cm" in answer
        for _ in range(75):
            session.runtime.step()
        answer = session.ask("Where is parcel 01 now?")["answer"]
        assert "parcel_01 was observed at x=150 cm, y=390 cm" in answer
        answer = session.ask("Where was parcel 01 before, and what changed?")["answer"]
        assert "Observed transition for parcel_01" in answer
        assert "inspection_station" in answer and "packing_station" in answer
        first_run = session.runtime.run_id
        # Simulate a checkpoint written before observed identities were registered.
        session.agent.memory._conn.execute(
            "DELETE FROM aliases WHERE canonical_entity_id = 'parcel_01'"
        )
        session.agent.memory._conn.commit()
        saved, image = session.persisted_state(), session.agent.memory.export_snapshot()
        session.restore(saved, image)
        assert not any(f["subject"] == "building_1" for f in session.snapshot()["active_facts"])
        assert "parcel_01 was observed" in session.ask("Where is parcel 01 now?")["answer"]
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
