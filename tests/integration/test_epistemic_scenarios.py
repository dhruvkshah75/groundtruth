"""End-to-end integration test suite demonstrating all 10 required epistemic scenarios.

Covers:
1. Scenario A Baseline: Map clear vs. LiDAR blocked reality conflict -> atomic belief revision
2. Scenario A Agreement: Map clear and LiDAR clear -> verified state
3. Scenario A Unverifiable: Sensor capability unavailable -> safe state preservation
4. Scenario B Perspectives: User (Red) vs. Egocentric (Brown) vs. Historical (Blue from bot_02)
5. Scenario B Neutral Light: Camera registers intrinsic red under white lighting
6. Declarative Integrity: Contradictory sources coexist in memory without silent overwrite
7. Disambiguation: Ambiguous entity mention triggers structured clarification request
8. Safe Fallback: Unrecognized entity returns safe fallback without hallucination
9. Epistemic Audit: Audit explanation query retrieves revision rationale and policy rule
10. Multi-Turn Lifecycle: Sequential queries demonstrate persistent belief revision and graph sync
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent import GroundedAgent
from src.contracts import FactAssertion, FactQuery, SpatialContext
from src.declarative.memory_repository import MemoryRepository
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import (
    AmbientLight,
    Obstacle,
    RobotState,
    SimulatedObject,
    WorldState,
)

_OBS_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _create_base_world() -> WorldState:
    return WorldState(
        robot=RobotState(
            robot_id="robot_1",
            x_cm=0.0,
            y_cm=0.0,
            direction="north",
            location="room_101",
            frame_of_reference="robot_base",
        ),
        light=AmbientLight(intensity=1.0, color_cast="white"),
    )


# ---------------------------------------------------------------------------
# Test 1: Scenario A Baseline (Map clear, LiDAR blocked -> belief revised)
# ---------------------------------------------------------------------------
def test_scenario_a_grounded_conflict_resolution() -> None:
    """Map says clear, live LiDAR scans 12cm obstruction -> triggers atomic belief revision."""
    world = _create_base_world()
    # Place obstacle 12cm ahead of robot at (0, 0) facing north
    obstacle = Obstacle(
        obstacle_id="obs_01",
        x_cm=0.0,
        y_cm=12.0,
        location="room_101",
        active=True,
    )
    world.obstacles["obs_01"] = obstacle
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        mem.add_alias("front route", "route_A")
        # Pre-seed static map assertion: route_A status_is clear
        initial_fact = mem.record_fact(
            FactAssertion(
                subject="route_A",
                predicate="status_is",
                object="clear",
                source_agent="static_map",
                confidence_score=0.95,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            )
        )

        agent = GroundedAgent.create(memory=mem, environment=env)

        # Pre-condition: active graph projection has route_A status_is clear
        pre_graph = agent.graph.get_graph()
        assert pre_graph.has_edge("route_A", "clear", key=str(initial_fact.fact_id))

        response = agent.ask("Is the front route clear to move forward?")

        # 1. Epistemic status & natural language output
        assert response.status == "grounded_conflict_resolved"
        assert (
            "No, my static mapping says it is clear, but my live LiDAR readings indicate "
            "a physical obstruction at 12.0cm right now" in response.answer
        )

        # 2. Atomic belief revision verified
        assert len(response.revisions) == 1
        rev = response.revisions[0]
        assert rev.audit_event.policy_rule == "lidar_overrides_map"
        assert rev.successor.object == "blocked"
        assert rev.successor.source_agent == "lidar_sensor"

        # 3. Memory storage state verified
        active_facts = mem.query_facts(FactQuery(subject="route_A", active_only=True))
        assert len(active_facts) == 1
        assert active_facts[0].object == "blocked"
        assert active_facts[0].superseded_by is None

        # Predecessor fact is durable in SQLite with superseded_by pointing to successor
        all_facts = mem.query_facts(FactQuery(subject="route_A", active_only=False))
        assert len(all_facts) == 2
        pred = next(f for f in all_facts if f.object == "clear")
        assert pred.superseded_by == rev.successor.fact_id

        # 4. Graph cache invalidation & projection update
        post_graph = agent.graph.get_graph()
        assert not post_graph.has_edge("route_A", "clear", key=str(initial_fact.fact_id))
        assert post_graph.has_edge("route_A", "blocked", key=str(rev.successor.fact_id))


# ---------------------------------------------------------------------------
# Test 2: Scenario A Agreement (Map clear, LiDAR clear -> verified)
# ---------------------------------------------------------------------------
def test_scenario_a_verified_agreement() -> None:
    """Map says clear, live LiDAR scans clear -> confirms route without revision."""
    world = _create_base_world()
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        mem.add_alias("front route", "route_A")
        mem.record_fact(
            FactAssertion(
                subject="route_A",
                predicate="status_is",
                object="clear",
                source_agent="static_map",
                confidence_score=0.95,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            )
        )

        agent = GroundedAgent.create(memory=mem, environment=env)
        response = agent.ask("Is the front route clear to move forward?")

        assert response.status == "verified"
        assert "Yes, the route is clear" in response.answer
        assert len(response.revisions) == 0


# ---------------------------------------------------------------------------
# Test 3: Scenario A Sensor Failure / Missing LiDAR -> unverifiable
# ---------------------------------------------------------------------------
def test_scenario_a_sensor_unavailable_unverifiable() -> None:
    """When LiDAR is absent, route status is marked unverifiable without assumptions."""
    world = _create_base_world()

    class NoLidarEnvironment(MockEnvironment):
        def get_capabilities(self):
            return [cap for cap in super().get_capabilities() if cap.name != "lidar_scan"]

    env = NoLidarEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        mem.add_alias("front route", "route_A")
        mem.record_fact(
            FactAssertion(
                subject="route_A",
                predicate="status_is",
                object="clear",
                source_agent="static_map",
                confidence_score=0.95,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            )
        )

        agent = GroundedAgent.create(memory=mem, environment=env)
        response = agent.ask("Is the front route clear?")

        assert response.status == "unverifiable"
        assert response.plan_verifiable is False
        assert "Current physical state cannot be verified" in response.answer
        assert "Preserving stored memory without ungrounded assumptions" in response.answer
        assert len(response.revisions) == 0


# ---------------------------------------------------------------------------
# Test 4: Scenario B Full Perspective Tracking (Theory of Mind)
# ---------------------------------------------------------------------------
def test_scenario_b_multi_perspective_tracking() -> None:
    """Isolates User (Red), Egocentric (Brown under yellow light), and History (Blue)."""
    world = _create_base_world()
    # Physical object has intrinsic color red
    target_box = SimulatedObject(
        object_id="box_01",
        label="box",
        intrinsic_color="red",
        location="room_101",
        x_cm=10.0,
        y_cm=10.0,
    )
    world.objects["box_01"] = target_box
    # Ambient lighting has a yellow color cast
    world.light = AmbientLight(intensity=0.8, color_cast="yellow")
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        mem.add_alias("red box", "box_01")
        # Historical provenance: bot_02 painted the box blue
        mem.record_fact(
            FactAssertion(
                subject="box_01",
                predicate="painted_color_is",
                object="blue",
                source_agent="bot_02",
                confidence_score=1.0,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101"),
                evidence={"action": "painted_blue"},
            )
        )

        agent = GroundedAgent.create(memory=mem, environment=env)
        response = agent.ask(
            "Can you see the red box? What color do different perspectives think it is?"
        )

        assert response.status == "perspectives_tracked"
        assert response.perspectives is not None

        # Check the 3 distinct perspectives
        assert "Red" in response.perspectives["user_perspective"]
        assert "Brown" in response.perspectives["egocentric_perspective"]
        assert "yellow" in response.perspectives["egocentric_perspective"]
        assert "Blue" in response.perspectives["historical_perspective"]
        assert "bot_02" in response.perspectives["historical_perspective"]

        # Explanation contains all three perspectives clearly broken down
        assert "User Perspective: Expects a red target" in response.answer
        assert "Egocentric Perspective: Currently senses brown" in response.answer
        assert (
            "Historical/Third-Party Perspective: Validated as blue via logged "
            "provenance update from 'bot_02'" in response.answer
        )


# ---------------------------------------------------------------------------
# Test 5: Scenario B Neutral Lighting Agreement
# ---------------------------------------------------------------------------
def test_scenario_b_neutral_lighting_agreement() -> None:
    """Under neutral white lighting, camera observation matches intrinsic red."""
    world = _create_base_world()
    target_box = SimulatedObject(
        object_id="box_01",
        label="box",
        intrinsic_color="red",
        location="room_101",
        x_cm=10.0,
        y_cm=10.0,
    )
    world.objects["box_01"] = target_box
    world.light = AmbientLight(intensity=1.0, color_cast="white")
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        mem.add_alias("red box", "box_01")
        agent = GroundedAgent.create(memory=mem, environment=env)
        response = agent.ask("What color is the red box from current perspective?")

        assert response.status == "perspectives_tracked"
        assert response.perspectives is not None
        assert "Red" in response.perspectives["egocentric_perspective"]


# ---------------------------------------------------------------------------
# Test 6: Declarative Integrity - Contradictory Sources Coexist in Memory
# ---------------------------------------------------------------------------
def test_contradictory_sources_coexist_without_silent_overwrite() -> None:
    """Contradictory assertions from different agents coexist in SQLite until explicit revision."""
    with MemoryRepository(":memory:") as mem:
        fact1 = mem.record_fact(
            FactAssertion(
                subject="box_01",
                predicate="color_is",
                object="red",
                source_agent="bot_01",
                confidence_score=0.8,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101"),
            )
        )
        fact2 = mem.record_fact(
            FactAssertion(
                subject="box_01",
                predicate="color_is",
                object="blue",
                source_agent="bot_02",
                confidence_score=0.9,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101"),
            )
        )

        active = mem.query_facts(FactQuery(subject="box_01", active_only=True))
        assert len(active) == 2
        assert {f.object for f in active} == {"red", "blue"}
        assert {f.source_agent for f in active} == {"bot_01", "bot_02"}
        assert fact1.fact_id != fact2.fact_id


# ---------------------------------------------------------------------------
# Test 7: Disambiguation - Ambiguous Entity Mention
# ---------------------------------------------------------------------------
def test_ambiguous_entity_mention_requests_clarification() -> None:
    """Ambiguous alias mention halts execution and returns structured clarification request."""
    world = _create_base_world()
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        # 'the box' maps to two different entities
        mem.add_alias("the box", "box_01")
        mem.add_alias("the box", "box_02")

        agent = GroundedAgent.create(memory=mem, environment=env)
        response = agent.ask("What is the color of the box?")

        assert response.status == "ambiguous"
        assert "Ambiguous entity mention" in response.answer
        assert "box_01" in response.answer
        assert "box_02" in response.answer
        assert len(response.revisions) == 0


# ---------------------------------------------------------------------------
# Test 8: Safe Fallback - Unrecognized Entity
# ---------------------------------------------------------------------------
def test_missing_unrecognized_entity_safe_fallback() -> None:
    """Unrecognized entity mention returns safe fallback without crashing or hallucinating."""
    world = _create_base_world()
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        agent = GroundedAgent.create(memory=mem, environment=env)
        response = agent.ask("What is the history of quantum_teleporter?")

        assert response.status == "unrecognized_entity"
        assert "Unrecognized entity" in response.answer
        assert len(response.revisions) == 0


# ---------------------------------------------------------------------------
# Test 9: Epistemic Audit - Audit Explanation Query
# ---------------------------------------------------------------------------
def test_audit_explanation_query() -> None:
    """Asking for audit history retrieves the full revision provenance trail and policy rule."""
    world = _create_base_world()
    obstacle = Obstacle(
        obstacle_id="obs_01",
        x_cm=0.0,
        y_cm=12.0,
        location="room_101",
        active=True,
    )
    world.obstacles["obs_01"] = obstacle
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        mem.add_alias("front route", "route_A")
        mem.add_alias("route_A", "route_A")
        mem.record_fact(
            FactAssertion(
                subject="route_A",
                predicate="status_is",
                object="clear",
                source_agent="static_map",
                confidence_score=0.95,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            )
        )

        agent = GroundedAgent.create(memory=mem, environment=env)

        # Trigger belief revision first
        agent.ask("Is the front route clear to move forward?")

        # Now query audit explanation
        audit_response = agent.ask("Why was route_A superseded?")

        assert audit_response.status == "audit_retrieved"
        assert len(audit_response.audit_events) >= 1
        event = audit_response.audit_events[0]
        assert event.policy_rule == "lidar_overrides_map"
        assert "Live LiDAR reading detected physical obstacle" in event.reason


# ---------------------------------------------------------------------------
# Test 10: Multi-Turn Continuous Epistemic Lifecycle
# ---------------------------------------------------------------------------
def test_end_to_end_multi_turn_epistemic_cycle() -> None:
    """Verifies sequential multi-turn query execution with persistent belief cache sync."""
    world = _create_base_world()
    obstacle = Obstacle(
        obstacle_id="obs_01",
        x_cm=0.0,
        y_cm=12.0,
        location="room_101",
        active=True,
    )
    world.obstacles["obs_01"] = obstacle
    env = MockEnvironment(world=world)

    with MemoryRepository(":memory:") as mem:
        mem.add_alias("front route", "route_A")
        mem.record_fact(
            FactAssertion(
                subject="route_A",
                predicate="status_is",
                object="clear",
                source_agent="static_map",
                confidence_score=0.95,
                observed_at=_OBS_TIME,
                context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            )
        )

        agent = GroundedAgent.create(memory=mem, environment=env)

        # Turn 1: Conflict detection and revision
        turn1 = agent.ask("Is the front route clear?")
        assert turn1.status == "grounded_conflict_resolved"
        assert len(turn1.revisions) == 1

        # Turn 2: Subsequent query confirms belief is now blocked without re-revising
        turn2 = agent.ask("Is the front route clear?")
        assert turn2.status == "verified"
        assert len(turn2.revisions) == 0
        assert "Route is blocked as recorded" in turn2.answer

        # Turn 3: Verify audit trail contains full history
        audit_trail = mem.get_audit_chain(turn1.revisions[0].successor.fact_id)
        assert len(audit_trail.facts) == 2
        assert len(audit_trail.events) == 1
