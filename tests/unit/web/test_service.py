from src.sensorimotor.world_models import SimulatedObject
from src.web.service import AgentService


def test_scenario_a_uses_live_lidar_and_records_revision() -> None:
    session = AgentService().get_session("39804313-d398-4f3c-a7ae-ea402866a8b8")
    session.load_scenario("scenario-a")

    baseline = session.snapshot()
    clear_fact = next(
        edge
        for edge in baseline["graph"]["edges"]
        if edge["source"] == "route_A"
        and edge["predicate"] == "status_is"
        and edge["target"] == "clear"
    )
    assert baseline["graph"]["node_count"] == 17
    assert not any(edge["target"] == "blocked" for edge in baseline["graph"]["edges"])

    result = session.ask(session.suggested_question)
    state = session.snapshot()

    assert result["status"] == "grounded_conflict_resolved"
    assert "12.0cm" in str(result["answer"])
    assert len(result["revisions"]) == 1
    assert result["sensor_telemetry"][0]["measurements"]["nearest_distance_cm"] == 12.0
    assert result["sensor_telemetry"][0]["context"]["frame_of_reference"] == "robot_base"
    route_status_history = [
        fact
        for fact in state["ledger"]
        if fact["subject"] == "route_A" and fact["predicate"] == "status_is"
    ]
    assert {fact["object"] for fact in route_status_history} == {"clear", "blocked"}
    assert any(fact["object"] == "clear" and fact["superseded_by"] for fact in route_status_history)
    blocked_fact = next(
        edge
        for edge in state["graph"]["edges"]
        if edge["source"] == "route_A"
        and edge["predicate"] == "status_is"
        and edge["target"] == "blocked"
    )
    clear_history = next(fact for fact in route_status_history if fact["object"] == "clear")
    assert clear_history["fact_id"] == clear_fact["fact_id"]
    assert clear_history["superseded_by"] == blocked_fact["fact_id"]
    assert not any(edge["target"] == "clear" for edge in state["graph"]["edges"])
    assert state["graph"]["node_count"] == 17
    assert len(state["audit_events"]) == 1


def test_scenario_b_keeps_prompt_camera_and_history_separate() -> None:
    session = AgentService().get_session("0910b52c-72cc-4bb5-9d9b-88a80b102c88")
    session.load_scenario("scenario-b")

    result = session.ask(session.suggested_question)
    perspectives = result["perspectives"]

    assert result["status"] == "perspectives_tracked"
    assert "red" in perspectives["user_perspective"].lower()
    assert "brown" in perspectives["egocentric_perspective"].lower()
    assert "yellow" in perspectives["egocentric_perspective"].lower()
    assert "blue" in perspectives["historical_perspective"].lower()
    assert "bot_02" in perspectives["historical_perspective"]
    observation = result["sensor_telemetry"][0]
    assert observation["capability"] == "camera_detect"
    assert observation["measurements"]["object_id"] == "box_01"


def test_missing_scenario_b_history_is_reported_without_invented_claim() -> None:
    session = AgentService().get_session("f25f98d0-d9f4-411f-b3f7-e4060d3e1000")
    session.agent.environment.replace_world(
        session.agent.environment.get_world_state().model_copy(
            update={
                "objects": {
                    "box_01": SimulatedObject(
                        object_id="box_01",
                        x_cm=10.0,
                        y_cm=10.0,
                        location="room_101",
                        intrinsic_color="red",
                    )
                }
            }
        )
    )

    result = session.ask("What color do you think the red box is?")
    historical = result["perspectives"]["historical_perspective"].lower()

    assert "no historical color record" in historical
    assert "bot_02" not in historical
    assert "blue" not in historical


def test_sessions_are_isolated_and_reset_clears_live_state() -> None:
    service = AgentService()
    first = service.get_session("5a710ec5-cf15-486b-8ab7-299df0e71ea1")
    second = service.get_session("3df7c1dd-0e07-4a2f-b189-3f8d376e8122")
    first.load_scenario("scenario-a")

    assert first.snapshot()["ledger"]
    assert second.snapshot()["graph"]["node_count"] == 17

    first.reset()
    reset_state = first.snapshot()
    assert reset_state["graph"]["node_count"] == 17
    assert reset_state["graph"]["edge_count"] == 17
    assert reset_state["history"] == []
    assert reset_state["environment"]["obstacles"] == {}
