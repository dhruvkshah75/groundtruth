"""Unit and integration tests for temporal object questions (Issue #32 / P2-08).

Validates:
1. All 5 modes (current, last_seen, at_time, history, changes) through the full pipeline.
2. Validated non-negative simulation time; missing/negative/malformed rejected without execution.
3. Future simulation-time queries produce uncertain status with explicit limitations.
4. Occlusion, later empty scans, sensor outages return last-seen/uncertain without guessing.
5. Entity disambiguation via aliases: ambiguous / missing entities clarify.
6. Chronological ordering, out-of-order ingestion handling, and cross-run isolation.
7. Grounded explanation without intermediate position interpolation.
8. Station relocation changes with revision history and physical transit caveat.
9. Guard against adversarial synthesis hallucinating coordinates or actions.
10. Prompt dataset covering supported paraphrases, ambiguity, and unsupported requests.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.agent import GroundedAgent
from src.contracts import (
    BoundingBox,
    CameraDetection,
    CameraScan,
    FactAssertion,
    ObservationRequest,
    ObservationSample,
    ObservationUnavailable,
    Point,
    Pose,
    SensorObservation,
    SpatialContext,
)
from src.declarative.memory_repository import MemoryRepository

EPOCH = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)


def make_pose(
    robot_id: str = "robot_01",
    location: str = "dock",
    x_cm: float = 100.0,
    y_cm: float = 200.0,
    direction: str = "north",
    frame_of_reference: str = "warehouse_map",
) -> Pose:
    """Create a valid Pose contract."""
    return Pose(
        robot_id=robot_id,
        x_cm=x_cm,
        y_cm=y_cm,
        direction=direction,
        location=location,
        frame_of_reference=frame_of_reference,
    )


def make_detection(
    object_id: str,
    x_cm: float = 120.0,
    y_cm: float = 220.0,
    apparent_color: str = "cardboard_brown",
    width_cm: float = 40.0,
    height_cm: float = 30.0,
    frame_of_reference: str = "warehouse_map",
) -> CameraDetection:
    """Create a valid CameraDetection contract."""
    return CameraDetection(
        object_id=object_id,
        apparent_color=apparent_color,
        position=Point(x_cm=x_cm, y_cm=y_cm),
        frame_of_reference=frame_of_reference,
        bounding_box=BoundingBox(width_cm=width_cm, height_cm=height_cm),
    )


def make_sample(
    run_id: UUID,
    sim_time_ms: int = 1000,
    sample_sequence: int = 0,
    capability: str = "camera_detect",
    target: str | None = None,
    detections: list[CameraDetection] | None = None,
    unavailable_reason: str | None = None,
    observer_pose: Pose | None = None,
    world_version: int = 1,
) -> ObservationSample:
    """Create an ObservationSample envelope."""
    pose = observer_pose or make_pose()
    captured_at = EPOCH + timedelta(milliseconds=sim_time_ms)
    request = ObservationRequest(capability=capability, target=target)

    if unavailable_reason is not None:
        result = ObservationUnavailable(
            capability=capability,
            reason=unavailable_reason,
            reported_at=captured_at,
            message="Observation unavailable.",
        )
    else:
        context = SpatialContext(
            observer=pose.robot_id,
            location=pose.location,
            frame_of_reference=pose.frame_of_reference,
            world_version=world_version,
        )
        objs = sorted(detections or [], key=lambda d: d.object_id)
        scan = CameraScan(objects=objs)
        result = SensorObservation(
            observation_id=uuid4(),
            sensor="camera_01",
            capability=capability,
            observed_at=captured_at,
            confidence_score=1.0,
            context=context,
            measurements=scan.model_dump(mode="json"),
        )

    return ObservationSample(
        sample_id=uuid4(),
        run_id=run_id,
        sample_sequence=sample_sequence,
        epoch_utc=EPOCH,
        sim_time_ms=sim_time_ms,
        captured_at=captured_at,
        sensor_id="camera_01",
        observer_pose=pose,
        world_version=world_version,
        request=request,
        result=result,
    )


def setup_agent(mem: MemoryRepository | None = None) -> GroundedAgent:
    """Set up GroundedAgent with pre-configured memory and environment."""
    repo = mem or MemoryRepository(":memory:")
    return GroundedAgent.create(memory=repo)


# ==============================================================================
# 1. Mode: current
# ==============================================================================


def test_current_mode_fresh_positive_sighting() -> None:
    """Current mode identifies fresh positive sighting with precise coordinates and citation."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    det = make_detection("parcel_01", x_cm=150.0, y_cm=250.0)
    sample = make_sample(run_id=run_id, sim_time_ms=1000, detections=[det])
    agent.memory.record_observation_sample(sample)

    response = agent.ask("Where is parcel_01 now?")

    assert response.status == "verified"
    assert "parcel_01 was observed at x=150 cm, y=250 cm in dock by robot_01" in response.answer
    assert "1000 ms" in response.answer
    assert str(sample.sample_id) in response.answer
    assert response.react_trace is not None
    assert response.react_trace.execution_summary["temporal_queries_count"] >= 1


def test_current_mode_resilient_to_subsequent_camera_outage() -> None:
    """Current mode falls back to last confirmed sighting upon later camera outage."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Positive sighting at 1000 ms
    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 120.0, 220.0)],
    )
    agent.memory.record_observation_sample(s1)

    # Later camera outage at 2000 ms
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=2000,
        sample_sequence=2,
        unavailable_reason="sensor_unavailable",
    )
    agent.memory.record_observation_sample(s2)

    response = agent.ask("Where is parcel_01 now?")

    assert response.status == "uncertain"
    assert "outage at simulation time 2000 ms (sensor_unavailable)" in response.answer
    assert (
        "Last confirmed sighting was at simulation time 1000 ms at x=120 cm, y=220 cm"
        in response.answer
    )
    assert str(s1.sample_id) in response.answer


def test_current_mode_resilient_to_subsequent_empty_scan() -> None:
    """Current mode notes subsequent empty scan and preserves last sighting without guessing."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Positive sighting at 1000 ms
    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 110.0, 210.0)],
    )
    agent.memory.record_observation_sample(s1)

    # Later empty scan at 2500 ms in aisle_3
    pose_empty = make_pose(location="aisle_3", x_cm=500.0, y_cm=500.0)
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=2500,
        sample_sequence=2,
        detections=[],
        observer_pose=pose_empty,
    )
    agent.memory.record_observation_sample(s2)

    response = agent.ask("Where is parcel_01 now?")

    assert response.status == "uncertain"
    assert (
        "subsequent camera scan at simulation time 2500 ms did not detect it in aisle_3"
        in response.answer
    )
    assert "Last confirmed sighting was at simulation time 1000 ms" in response.answer
    assert str(s1.sample_id) in response.answer


def test_current_mode_no_sightings_with_stored_belief() -> None:
    """Current mode cites stored memory belief when no physical sightings have occurred in run."""
    agent = setup_agent()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Stored fact from manifest/dispatch
    agent.memory.record_fact(
        FactAssertion(
            subject="parcel_01",
            predicate="located_at",
            object="shelf_B2",
            source_agent="inventory_system",
            confidence_score=0.9,
            observed_at=EPOCH,
            context=SpatialContext(observer="system", location="warehouse", world_version=1),
        )
    )

    response = agent.ask("Where is parcel_01 now?")

    assert response.status == "stored_belief"
    assert "No physical camera observations have been recorded for parcel_01" in response.answer
    assert "shelf_B2" in response.answer
    assert "inventory_system" in response.answer


# ==============================================================================
# 2. Mode: last_seen
# ==============================================================================


def test_last_seen_mode_with_subsequent_status() -> None:
    """Last_seen mode returns last positive sighting with age and verification status."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Positive sighting at 1000 ms
    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    agent.memory.record_observation_sample(s1)

    # Subsequent scan at 3000 ms (empty)
    s2 = make_sample(run_id=run_id, sim_time_ms=3000, sample_sequence=2, detections=[])
    agent.memory.record_observation_sample(s2)

    response = agent.ask("When did you last see parcel_01?")

    assert response.status == "verified"
    assert (
        "parcel_01 was last seen at simulation time 1000 ms at x=100 cm, y=200 cm"
        in response.answer
    )
    assert "age: 2000 ms" in response.answer
    assert "Subsequent camera scan at 3000 ms did not detect parcel_01" in response.answer
    assert str(s1.sample_id) in response.answer


def test_last_seen_mode_no_sightings() -> None:
    """Last_seen returns no_data when entity has never been observed."""
    agent = setup_agent()
    agent.memory.add_alias("parcel_01", "parcel_01")

    response = agent.ask("When did you last see parcel_01?")
    assert response.status == "no_data"
    assert "No positive sightings of parcel_01 have been recorded" in response.answer


# ==============================================================================
# 3. Mode: at_time
# ==============================================================================


def test_at_time_mode_preceding_sighting_no_interpolation() -> None:
    """'Where was it at 7 seconds?' identifies actual preceding sighting and never interpolates."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Sighting at 5000 ms
    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=5000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 150.0, 250.0)],
    )
    agent.memory.record_observation_sample(s1)

    # Later scan at 8000 ms
    s2 = make_sample(run_id=run_id, sim_time_ms=8000, sample_sequence=2, detections=[])
    agent.memory.record_observation_sample(s2)

    response = agent.ask("Where was parcel_01 at 7 seconds?")

    assert response.status == "verified"
    assert "At simulation time 7000 ms" in response.answer
    assert "captured at 5000 ms (age: 2000 ms)" in response.answer
    assert "x=150 cm, y=250 cm" in response.answer
    assert "Note: No intermediate position is interpolated at 7000 ms" in response.answer
    assert str(s1.sample_id) in response.answer


def test_at_time_mode_future_time_uncertain() -> None:
    """at_time for timestamp beyond available ledger returns uncertain status."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=3000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 100.0, 100.0)],
    )
    agent.memory.record_observation_sample(s1)

    response = agent.ask("Where was parcel_01 at 10 seconds?")

    assert response.status == "uncertain"
    assert "in the future relative to available observation ledger evidence" in response.answer
    assert "latest recorded: 3000 ms" in response.answer


def test_at_time_mode_negative_time_rejected() -> None:
    """Negative simulation time is rejected at planning without tool execution."""
    agent = setup_agent()
    agent.memory.add_alias("parcel_01", "parcel_01")

    response = agent.ask("Where was parcel_01 at -5 seconds?")

    # Intent validation fails and falls back to unsupported without executing operations
    assert response.status == "unsupported"
    assert response.react_trace is not None
    assert response.react_trace.execution_summary["temporal_queries_count"] == 0


# ==============================================================================
# 4. Mode: history
# ==============================================================================


def test_history_mode_chronological_timeline() -> None:
    """History mode returns bounded chronological timeline of all sightings."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Ingest 3 sightings across time
    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        observer_pose=make_pose(location="dock"),
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=3000,
        sample_sequence=2,
        observer_pose=make_pose(location="corridor"),
        detections=[make_detection("parcel_01", 300.0, 400.0)],
    )
    s3 = make_sample(
        run_id=run_id,
        sim_time_ms=6000,
        sample_sequence=3,
        observer_pose=make_pose(location="packing"),
        detections=[make_detection("parcel_01", 600.0, 700.0)],
    )

    for s in [s1, s2, s3]:
        agent.memory.record_observation_sample(s)

    response = agent.ask("Show sighting history of parcel_01")

    assert response.status == "verified"
    assert "Sighting timeline for parcel_01:" in response.answer
    assert "1000 ms: at x=100 cm, y=200 cm in dock" in response.answer
    assert "3000 ms: at x=300 cm, y=400 cm in corridor" in response.answer
    assert "6000 ms: at x=600 cm, y=700 cm in packing" in response.answer
    assert str(s1.sample_id) in response.answer
    assert str(s2.sample_id) in response.answer
    assert str(s3.sample_id) in response.answer


# ==============================================================================
# 5. Mode: changes
# ==============================================================================


def test_changes_mode_visible_station_relocation() -> None:
    """Changes mode links sightings, revision records, and appends mandatory transit caveat."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Ingest sightings at station_1, then station_2
    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        observer_pose=make_pose(location="station_1"),
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=4000,
        sample_sequence=2,
        observer_pose=make_pose(location="station_2"),
        detections=[make_detection("parcel_01", 500.0, 600.0)],
    )
    agent.memory.record_observation_sample(s1)
    agent.memory.record_observation_sample(s2)

    # Record Tier 1 revision fact and audit trail
    f1 = agent.memory.record_fact(
        FactAssertion(
            subject="parcel_01",
            predicate="located_at",
            object="station_1",
            source_agent="operator",
            confidence_score=1.0,
            observed_at=EPOCH,
            context=SpatialContext(observer="system", location="station_1", world_version=1),
        )
    )
    f2_assert = FactAssertion(
        subject="parcel_01",
        predicate="located_at",
        object="station_2",
        source_agent="robot_transfer",
        confidence_score=1.0,
        observed_at=EPOCH + timedelta(seconds=4),
        context=SpatialContext(observer="system", location="station_2", world_version=2),
    )
    agent.memory.record_revision(
        old_fact_id=f1.fact_id,
        replacement=f2_assert,
        reason="Transferred by AGV",
        policy_rule="station_handover_policy",
        revised_at=EPOCH + timedelta(seconds=4),
    )

    response = agent.ask("Where was parcel_01 before, and what changed?")

    assert response.status == "verified"
    assert "Observed transition for parcel_01:" in response.answer
    assert "sighted at station_1 at 1000 ms" in response.answer
    assert "subsequently sighted at station_2 at 4000 ms" in response.answer
    assert "Transferred by AGV" in response.answer
    assert "station_handover_policy" in response.answer
    # Mandatory transit caveat from Issue #32:
    assert (
        "Note: Two sightings at distinct locations demonstrate that a transition occurred, "
        "but do not prove continuous path, exact transit duration, or physical relocation cause."
    ) in response.answer


def test_changes_mode_no_transition() -> None:
    """Changes mode reports no location change when sightings remain stationary."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        observer_pose=make_pose(location="station_1"),
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=3000,
        sample_sequence=2,
        observer_pose=make_pose(location="station_1"),
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    agent.memory.record_observation_sample(s1)
    agent.memory.record_observation_sample(s2)

    response = agent.ask("Where was parcel_01 before, and what changed?")

    assert response.status == "verified"
    assert "No location change observed for parcel_01" in response.answer
    assert "across 2 observation(s) from 1000 ms to 3000 ms" in response.answer


# ==============================================================================
# 6. Alias Ambiguity and Entity Disambiguation
# ==============================================================================


def test_alias_ambiguity_returns_clarification() -> None:
    """Ambiguous alias matching multiple entities clarifies without querying arbitrary objects."""
    agent = setup_agent()
    agent.memory.add_alias("red parcel", "parcel_01")
    agent.memory.add_alias("red parcel", "parcel_02")

    response = agent.ask("Where is the red parcel now?")

    assert response.status == "ambiguous"
    assert (
        "Ambiguous entity mention in query. Candidates found: [parcel_01, parcel_02]"
        in response.answer
    )
    assert "Please clarify which entity you mean" in response.answer
    assert response.react_trace is not None
    assert response.react_trace.execution_summary["temporal_queries_count"] == 0


def test_missing_alias_returns_unrecognized() -> None:
    """Unrecognized entity mention returns safe failure without arbitrary querying."""
    agent = setup_agent()

    response = agent.ask("Where is parcel_99 now?")

    assert response.status == "unrecognized_entity"
    assert "Unrecognized entity" in response.answer
    assert response.react_trace is not None
    assert response.react_trace.execution_summary["temporal_queries_count"] == 0


# ==============================================================================
# 7. Cross-run isolation & Out-of-order tie breaking
# ==============================================================================


def test_cross_run_isolation() -> None:
    """Sightings from an older run are not leaked when querying a new run."""
    agent = setup_agent()
    run_a = uuid4()
    run_b = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Sighting in run A
    s_a = make_sample(
        run_id=run_a, sim_time_ms=1000, detections=[make_detection("parcel_01", 10.0, 20.0)]
    )
    agent.memory.record_observation_sample(s_a)

    # In run B, only empty scan exists
    s_b = make_sample(run_id=run_b, sim_time_ms=2000, detections=[])
    agent.memory.record_observation_sample(s_b)

    # When querying, latest run is run B
    response = agent.ask("Where is parcel_01 now?")

    # In run B, parcel_01 was never sighted
    assert response.status == "no_data"
    assert "No observations or memory records found for parcel_01" in response.answer


def test_out_of_order_sample_delivery_chronological_ordering() -> None:
    """Ledger handles out-of-order sample arrivals and returns correct chronological history."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Ingest sim_time_ms=3000 before sim_time_ms=1000
    s_late = make_sample(
        run_id=run_id,
        sim_time_ms=3000,
        sample_sequence=2,
        detections=[make_detection("parcel_01", 300.0, 300.0)],
    )
    s_early = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 100.0, 100.0)],
    )

    agent.memory.record_observation_sample(s_late)
    agent.memory.record_observation_sample(s_early)

    response = agent.ask("Show sighting history of parcel_01")

    assert response.status == "verified"
    idx_1000 = response.answer.find("1000 ms")
    idx_3000 = response.answer.find("3000 ms")
    assert 0 <= idx_1000 < idx_3000


# ==============================================================================
# 8. Adversarial LLM synthesis guard
# ==============================================================================


class AdversarialProvider:
    """Simulates an LLM attempting to fabricate actions, coordinates, or intermediate paths."""

    last_tool_name = "object_location_query"
    last_tool_call_id = "call_fake_0"
    last_tool_args: dict[str, Any] = {}
    model_name = "fake_adversarial_model"

    def propose_intent(self, question: str) -> dict[str, Any]:
        return {
            "intent": "object_location_query",
            "mode": "at_time",
            "at_sim_time_ms": 7000,
            "entity_mentions": ["parcel_01"],
            "user_question": question,
        }

    def generate_grounded_explanation(
        self,
        user_question: str,
        tool_name: str,
        tool_call_id: str,
        tool_result: dict[str, Any],
    ) -> str:
        # Maliciously invents an intermediate interpolated point at 7000 ms
        return "I can confirm parcel_01 was at x=999 cm in transit at 7000 ms."


def test_adversarial_synthesis_falls_back_to_deterministic_grounding() -> None:
    """Adversarial LLM proposing ungrounded path fails closed to deterministic fallback."""
    repo = MemoryRepository(":memory:")
    run_id = uuid4()
    repo.add_alias("parcel_01", "parcel_01")

    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=5000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 150.0, 250.0)],
    )
    s2 = make_sample(run_id=run_id, sim_time_ms=8000, sample_sequence=2, detections=[])
    repo.record_observation_sample(s1)
    repo.record_observation_sample(s2)

    fake_prov = AdversarialProvider()
    agent = GroundedAgent.create(memory=repo, provider=fake_prov)

    response = agent.ask("Where was parcel_01 at 7 seconds?")

    # Hallucinated coordinates must be rejected and deterministic fallback used
    assert "x=999" not in response.answer
    assert "captured at 5000 ms" in response.answer
    assert "x=150 cm, y=250 cm" in response.answer
    assert "Note: No intermediate position is interpolated at 7000 ms" in response.answer
    assert response.react_trace is not None
    assert response.react_trace.explanation_source == "deterministic_fallback"


# ==============================================================================
# 9. Prompt Dataset: paraphrases, ambiguity, and unsupported requests
# ==============================================================================

PROMPT_DATASET = [
    # Paraphrases for current mode
    ("Where is parcel_01 now?", "current", "verified"),
    ("What is the current location of parcel_01?", "current", "verified"),
    ("Where is parcel_01 located right now?", "current", "verified"),
    # Paraphrases for last_seen mode
    ("When did you last see parcel_01?", "last_seen", "verified"),
    ("Where was parcel_01 last seen?", "last_seen", "verified"),
    ("When did you last observe parcel_01?", "last_seen", "verified"),
    # Paraphrases for at_time mode
    ("Where was parcel_01 at 4 seconds?", "at_time", "verified"),
    ("Where was parcel_01 at 4000 ms?", "at_time", "verified"),
    ("What was the position of parcel_01 at 4s?", "at_time", "verified"),
    # Paraphrases for history mode
    ("Show sighting history of parcel_01", "history", "verified"),
    ("Give me the timeline of sightings for parcel_01", "history", "verified"),
    # Paraphrases for changes mode
    ("Where was parcel_01 before, and what changed?", "changes", "verified"),
    ("Has parcel_01 moved?", "changes", "verified"),
    # Ambiguous entity mention
    ("Where is the red parcel now?", None, "ambiguous"),
    # Unsupported requests
    ("Predict where parcel_01 will move tomorrow", None, "unsupported"),
    ("Navigate parcel_01 to the dock", None, "unsupported"),
]


@pytest.mark.parametrize("prompt,expected_mode,expected_status", PROMPT_DATASET)
def test_prompt_dataset_coverage(
    prompt: str,
    expected_mode: str | None,
    expected_status: str,
) -> None:
    """Validate prompt dataset covering paraphrases, ambiguity, and unsupported requests."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")
    agent.memory.add_alias("red parcel", "parcel_01")
    agent.memory.add_alias("red parcel", "parcel_02")

    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=2000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=5000,
        sample_sequence=2,
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    agent.memory.record_observation_sample(s1)
    agent.memory.record_observation_sample(s2)

    response = agent.ask(prompt)

    assert response.status == expected_status
    if expected_mode is not None:
        assert response.react_trace is not None
        assert response.react_trace.arguments.get("mode") == expected_mode


# ==============================================================================
# 10. Reviews & Edge Cases: Aliases across modes, Ties, Stale, & Cross-Run
# ==============================================================================


def test_aliases_across_all_temporal_modes() -> None:
    """Valid alias resolves to canonical ID and locates detections without inventing coordinates."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("red parcel", "parcel_01")

    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=2000,
        sample_sequence=1,
        observer_pose=make_pose(location="station_A"),
        detections=[make_detection("parcel_01", 150.0, 250.0)],
    )
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=5000,
        sample_sequence=2,
        observer_pose=make_pose(location="station_B"),
        detections=[make_detection("parcel_01", 400.0, 500.0)],
    )
    agent.memory.record_observation_sample(s1)
    agent.memory.record_observation_sample(s2)

    # 1. current mode
    r_curr = agent.ask("Where is red parcel now?")
    assert r_curr.status == "verified"
    assert "x=400 cm, y=500 cm" in r_curr.answer
    assert "x=0 cm, y=0 cm" not in r_curr.answer

    # 2. last_seen mode
    r_ls = agent.ask("When did you last see red parcel?")
    assert r_ls.status == "verified"
    assert "x=400 cm, y=500 cm" in r_ls.answer

    # 3. at_time mode
    r_at = agent.ask("Where was red parcel at 2 seconds?")
    assert r_at.status == "verified"
    assert "x=150 cm, y=250 cm" in r_at.answer
    assert "x=0 cm, y=0 cm" not in r_at.answer

    # 4. history mode
    r_hist = agent.ask("Show sighting history of red parcel")
    assert r_hist.status == "verified"
    assert "150" in r_hist.answer and "400" in r_hist.answer

    # 5. changes mode
    r_chg = agent.ask("Has red parcel moved?")
    assert r_chg.status == "verified"
    assert "sighted at station_A" in r_chg.answer
    assert "subsequently sighted at station_B" in r_chg.answer


def test_missing_detection_yields_uncertainty_or_no_data_never_invented_coords() -> None:
    """Missing detection in a sample does not fabricate (0, 0) coordinates."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Sample only contains other_box, parcel_01 is NOT detected
    s = make_sample(
        run_id=run_id,
        sim_time_ms=2000,
        sample_sequence=1,
        detections=[make_detection("other_box", 300.0, 400.0)],
    )
    agent.memory.record_observation_sample(s)

    r_curr = agent.ask("Where is parcel_01 now?")
    assert r_curr.status in ("uncertain", "no_data")
    assert "x=0" not in r_curr.answer

    r_at = agent.ask("Where was parcel_01 at 3000 ms?")
    assert r_at.status in ("uncertain", "no_data")
    assert "x=0" not in r_at.answer


def test_unspecified_object_reference_does_not_default_to_parcel_01() -> None:
    """Where is it? does not query parcel_01 and returns safe fallback without assumption."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")
    s = make_sample(
        run_id=run_id,
        sim_time_ms=2000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    agent.memory.record_observation_sample(s)

    response = agent.ask("Where is it?")
    assert response.status in ("unrecognized_entity", "ambiguous", "unsupported")
    assert "100" not in response.answer


def test_equal_time_sequence_tie_outage_or_non_detection_marks_uncertain() -> None:
    """Later sample at same sim_time_ms with higher sequence overrides earlier detection."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Sighting at sequence 1
    s_detect = make_sample(
        run_id=run_id,
        sim_time_ms=5000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    # Non-detection scan at sequence 2 (same millisecond)
    s_empty = make_sample(
        run_id=run_id,
        sim_time_ms=5000,
        sample_sequence=2,
        detections=[],
    )
    agent.memory.record_observation_sample(s_detect)
    agent.memory.record_observation_sample(s_empty)

    response = agent.ask("Where is parcel_01 now?")
    assert response.status == "uncertain"
    assert "did not detect" in response.answer


def test_capture_time_stale_evidence_marks_current_uncertain_and_advances_last_seen_age() -> None:
    """Request capture time advances evidence age and flags current location as stale."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    s = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    agent.memory.record_observation_sample(s)

    # World has advanced to 12000 ms (age: 11000 ms > 1000 ms freshness threshold)
    intent_curr = agent.interpret("Where is parcel_01 now?")
    resp_curr = agent.capture("Where is parcel_01 now?", intent_curr, capture_time_ms=12000)()
    assert resp_curr.status == "uncertain"
    assert "stale" in resp_curr.answer
    assert "11000 ms" in resp_curr.answer

    # last_seen age calculates against capture time 12000 ms, not 0 ms
    intent_ls = agent.interpret("When did you last see parcel_01?")
    resp_ls = agent.capture("When did you last see parcel_01?", intent_ls, capture_time_ms=12000)()
    assert resp_ls.status == "verified"
    assert "age: 11000 ms" in resp_ls.answer


def test_cross_run_reset_isolation() -> None:
    """Resetting run isolates queries so Run 2 does not select Run 1's historical evidence."""
    agent = setup_agent()
    run_id_1 = uuid4()
    run_id_2 = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Run 1 ends at 20000 ms
    s_run1 = make_sample(
        run_id=run_id_1,
        sim_time_ms=20000,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 888.0, 888.0)],
    )
    agent.memory.record_observation_sample(s_run1)

    # Run 2 starts after reset at 500 ms
    s_run2 = make_sample(
        run_id=run_id_2,
        sim_time_ms=500,
        sample_sequence=1,
        detections=[make_detection("parcel_01", 111.0, 222.0)],
    )
    agent.memory.record_observation_sample(s_run2)

    # Implicit query with sensors bound to Run 2
    class MockRuntimeSensors:
        run_id = run_id_2
        sim_time_ms = 500

        def observe(self, req: Any) -> Any:
            return ObservationUnavailable(
                capability=req.capability,
                reason="sensor_unavailable",
                message="Mock offline",
                reported_at=datetime.now(UTC),
            )

    intent = agent.interpret("Where is parcel_01 now?")
    response = agent.capture("Where is parcel_01 now?", intent, sensors=MockRuntimeSensors())()

    assert response.status == "verified"
    assert "x=111 cm, y=222 cm" in response.answer
    assert "888 cm" not in response.answer


def test_legacy_robot_pose_questions_not_classified_as_object_queries() -> None:
    """Robot pose questions (e.g. 'What is your position?') preserve current_robot_pose intent."""
    from src.procedural.rule_based_provider import RuleBasedIntentProvider

    provider = RuleBasedIntentProvider()

    pose_queries = [
        "What is your position?",
        "What is your current position?",
        "Where are you?",
        "Where are you located?",
        "What is your location?",
        "Give me your pose",
    ]
    for q in pose_queries:
        proposal = provider.propose_intent(q)
        assert proposal["intent"] == "current_robot_pose", f"Failed for query '{q}': got {proposal}"
        assert proposal["entity_mentions"] == ["robot_1"]

    agent = setup_agent()
    r = agent.ask("What is your position?")
    assert r.status == "verified"
    assert r.react_trace is not None
    assert r.react_trace.arguments.get("intent") == "current_robot_pose"


def test_observer_location_change_does_not_invent_object_movement() -> None:
    """Robot moving between stations observing same coordinates does not trigger transition."""
    agent = setup_agent()
    run_id = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Sample 1: Robot at dock, parcel at (100, 200)
    s1 = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        sample_sequence=1,
        observer_pose=make_pose(location="dock"),
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    # Sample 2: Robot at station_B, parcel still at (100, 200)
    s2 = make_sample(
        run_id=run_id,
        sim_time_ms=3000,
        sample_sequence=2,
        observer_pose=make_pose(location="station_B"),
        detections=[make_detection("parcel_01", 100.0, 200.0)],
    )
    agent.memory.record_observation_sample(s1)
    agent.memory.record_observation_sample(s2)

    response = agent.ask("Has parcel_01 moved?")
    assert response.status == "verified"
    assert "No location change observed" in response.answer
    assert "Observed transition" not in response.answer


def test_stored_belief_scoped_to_run_earlier_run_cannot_establish_location() -> None:
    """Stored belief from earlier run cannot establish location for a new run without sightings."""
    agent = setup_agent()
    run_1 = uuid4()
    run_2 = uuid4()
    agent.memory.add_alias("parcel_01", "parcel_01")

    # Stored fact explicitly tagged with run_1
    agent.memory.record_fact(
        FactAssertion(
            subject="parcel_01",
            predicate="located_at",
            object="shelf_A1",
            source_agent="inventory_system",
            confidence_score=0.9,
            observed_at=EPOCH,
            context=SpatialContext(location="shelf_A1", extra_context={"run_id": str(run_1)}),
        )
    )

    class MockRun2Sensors:
        run_id = run_2
        sim_time_ms = 100

        def observe(self, req: Any) -> Any:
            return ObservationUnavailable(
                capability=req.capability,
                reason="sensor_unavailable",
                message="Mock offline",
                reported_at=datetime.now(UTC),
            )

    intent = agent.interpret("Where is parcel_01 now?")
    response = agent.capture("Where is parcel_01 now?", intent, sensors=MockRun2Sensors())()

    # Must report uncertainty about provenance, not verified/stored_belief for run 2
    assert response.status in ("uncertain", "no_data")
    assert (
        "cannot establish a location for run" in response.answer
        or "shelf_A1" not in response.answer
    )


def test_time_words_and_historical_intent_preserved() -> None:
    """Word numbers parse in at_time, 'previously' maps to changes, and unparseable times reject."""
    from src.procedural.rule_based_provider import RuleBasedIntentProvider

    provider = RuleBasedIntentProvider()

    # 1. Word number in at_time mode
    p1 = provider.propose_intent("Where was parcel_01 at seven seconds?")
    assert p1["intent"] == "object_location_query"
    assert p1["mode"] == "at_time"
    assert p1["at_sim_time_ms"] == 7000

    # 2. 'previously' maps to changes
    p2 = provider.propose_intent("Where was parcel_01 previously?")
    assert p2["intent"] == "object_location_query"
    assert p2["mode"] == "changes"

    # 3. Unparseable time phrase rejects rather than falling through to current
    p3 = provider.propose_intent("Where was parcel_01 at unknown_time seconds?")
    assert p3["intent"] == "object_location_query"
    assert p3["mode"] == "at_time"
    assert p3["at_sim_time_ms"] is None


def test_negative_fractional_time_rejected() -> None:
    """Negative fractional simulation time (-0.1 ms) is rejected before integer truncation."""
    agent = setup_agent()
    agent.memory.add_alias("parcel_01", "parcel_01")

    response = agent.ask("Where was parcel_01 at -0.1 ms?")
    assert response.status == "unsupported"
    assert response.react_trace is not None
    assert response.react_trace.execution_summary["temporal_queries_count"] == 0
