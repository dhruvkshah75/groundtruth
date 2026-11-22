"""Ten distinct automated epistemic evaluation scenarios for Phase 2 (P2-12).

Scenarios:
1. Map-clear vs cart-blocked route followed by clearance, preserving both revisions.
2. Visible parcel relocation between known stations, with supported earlier/current location.
3. Parcel moves outside view; current position remains unverified and last sighting is retained.
4. Shelf occlusion and later reacquisition with unchanged opaque geometry.
5. Two similar parcels/ambiguous mention; clarification rather than identity merging.
6. User expectation vs lighting-affected camera perception vs historical maintenance claim.
7. Wrong-room/frame/observer/route sample cannot revise the current matching belief.
8. Sensor outage and stale evidence; no fake clear reading; recovery requires valid observation.
9. Delayed older observation after a newer sighting cannot become the latest belief.
10. Movement wholly between samples may be missed; agent never claims to have observed hidden event.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

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
from src.contracts.simulation import (
    LightMeasurement,
    MoveEvent,
    Rectangle,
    ScenarioDefinition,
    SceneEntity,
    SensorAvailabilityEvent,
)
from src.evaluation.models import (
    AudienceTruthSection,
    ExpectedQueryEvaluation,
    ReplayBundle,
    StaticMapConfig,
)
from src.procedural.temporal_belief_ingestion import (
    StaticRoute,
    StationZone,
)
from src.web.simulation_presets import warehouse_preset

EPOCH = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)


def _make_base_scenario(run_id: UUID) -> ScenarioDefinition:
    """Standard warehouse scene from the shared fixture."""
    return warehouse_preset().model_copy(deep=True)


def _make_default_static_map() -> StaticMapConfig:
    """Static routes and station zones for the evaluation environment."""
    return StaticMapConfig(
        zones=[
            StationZone.from_center(
                zone_id="packing_station",
                location="warehouse",
                frame_of_reference="warehouse_map",
                center_x_cm=80.0,
                center_y_cm=350.0,
                width_cm=60.0,
                height_cm=60.0,
            ),
            StationZone.from_center(
                zone_id="packing_station",
                location="packing_station",
                frame_of_reference="warehouse_map",
                center_x_cm=80.0,
                center_y_cm=350.0,
                width_cm=60.0,
                height_cm=60.0,
            ),
            StationZone.from_center(
                zone_id="inspection_station",
                location="warehouse",
                frame_of_reference="warehouse_map",
                center_x_cm=150.0,
                center_y_cm=390.0,
                width_cm=60.0,
                height_cm=60.0,
            ),
            StationZone.from_center(
                zone_id="inspection_station",
                location="inspection_station",
                frame_of_reference="warehouse_map",
                center_x_cm=150.0,
                center_y_cm=390.0,
                width_cm=60.0,
                height_cm=60.0,
            ),
        ],
        routes=[
            StaticRoute(
                route_id="route_A",
                location="warehouse",
                frame_of_reference="warehouse_map",
                start=Point(x_cm=80.0, y_cm=350.0),
                end=Point(x_cm=80.0, y_cm=450.0),
                corridor_width_cm=40.0,
            )
        ],
    )


def _make_sample(
    sample_id: UUID,
    run_id: UUID,
    seq: int,
    sim_time_ms: int,
    capability: str,
    observer_pose: Pose,
    measurements: dict | None = None,
    unavailable_reason: str | None = None,
    target: str | None = None,
    world_version: int = 1,
) -> ObservationSample:
    """Helper to build a deterministic observation sample."""
    captured_at = EPOCH + timedelta(milliseconds=sim_time_ms)
    request = ObservationRequest(
        capability=capability,
        target=target,
        parameters={"direction": "front"} if capability == "lidar_scan" else {},
    )

    if unavailable_reason:
        result = ObservationUnavailable(
            capability=capability,
            reason=unavailable_reason,
            reported_at=captured_at,
            message="Observation unavailable.",
        )
    else:
        context = SpatialContext(
            location=observer_pose.location,
            frame_of_reference=observer_pose.frame_of_reference,
            world_version=world_version,
            observer=observer_pose.robot_id,
        )
        result = SensorObservation(
            observation_id=sample_id,
            sensor=f"{capability}_01",
            capability=capability,
            observed_at=captured_at,
            confidence_score=1.0,
            context=context,
            measurements=measurements or {},
        )

    return ObservationSample(
        sample_id=sample_id,
        run_id=run_id,
        sample_sequence=seq,
        epoch_utc=EPOCH,
        sim_time_ms=sim_time_ms,
        captured_at=captured_at,
        sensor_id=f"{capability}_01",
        observer_pose=observer_pose,
        world_version=world_version,
        request=request,
        result=result,
    )


# ---------------------------------------------------------------------------
# Scenario 1: Map-clear vs cart-blocked route, followed by clearance
# ---------------------------------------------------------------------------
def scenario_01_map_clear_vs_cart_blocked() -> ReplayBundle:
    """Static map says clear, cart enters route, robot stops with audit trail, cart clears."""
    run_id = UUID("10000000-0000-0000-0000-000000000001")
    scene = _make_base_scenario(run_id)
    pose = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=360.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    scene.robot_pose = pose

    # Position existing cart_01 to block route_A at t=0
    cart = next(e for e in scene.entities if e.entity_id == "cart_01")
    cart.footprint.x_cm = 80.0
    cart.footprint.y_cm = 400.0

    # Cart cleared at t=500ms (finishes by t=600ms, clear by t=1000ms scan)
    cart_move = MoveEvent(
        kind="move",
        at_ms=500,
        sequence=0,
        entity_id="cart_01",
        to=Point(x_cm=180.0, y_cm=470.0),
        duration_ms=100,
    )
    scene.events = sorted([*scene.events, cart_move], key=lambda e: (e.at_ms, e.sequence))
    scene.patrol = [Point(x_cm=80.0, y_cm=360.0), Point(x_cm=80.0, y_cm=450.0)]

    # Initial static map fact asserting route_A is clear
    init_fact = FactAssertion(
        subject="route_A",
        predicate="status_is",
        object="clear",
        source_agent="static_map",
        confidence_score=0.95,
        observed_at=EPOCH,
        context=SpatialContext(
            location="warehouse",
            frame_of_reference="warehouse_map",
            extra_context={"run_id": str(run_id)},
        ),
        evidence={"run_id": str(run_id)},
    ).model_dump(mode="json")

    # Sample 1 (t=0): Front LiDAR blocked by cart at 20 cm
    s1 = _make_sample(
        sample_id=UUID("10000000-0000-0000-0000-000000000011"),
        run_id=run_id,
        seq=1,
        sim_time_ms=0,
        capability="lidar_scan",
        observer_pose=pose,
        measurements={
            "nearest_distance_cm": 20.0,
            "detected": True,
            "direction": "front",
            "status": "blocked",
        },
    )
    # Sample 2 (t=1000): Cart cleared, Front LiDAR reports clear
    s2 = _make_sample(
        sample_id=UUID("10000000-0000-0000-0000-000000000012"),
        run_id=run_id,
        seq=2,
        sim_time_ms=1000,
        capability="lidar_scan",
        observer_pose=pose,
        measurements={
            "nearest_distance_cm": None,
            "detected": False,
            "direction": "front",
            "status": "clear",
        },
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_01_blocked",
            prompt="Is the front route clear to move forward?",
            query_sim_time_ms=0,
            after_sample_sequence=1,
            expected_status="grounded",
            expected_revisions_count=1,
            expected_active_facts=[
                {"subject": "route_A", "predicate": "status_is", "object": "blocked"}
            ],
            unexpected_active_facts=[
                {"subject": "route_A", "predicate": "status_is", "object": "clear"}
            ],
            expected_revisions=[
                {
                    "predecessor_object": "clear",
                    "successor_object": "blocked",
                    "policy_rule": "lidar_observation_route_status",
                }
            ],
            expected_evidence_sample_ids=[s1.sample_id],
            conflict_or_uncertainty_rationale=(
                "Static map indicates route_A is clear, but live front LiDAR detects obstacle "
                "at 20.0 cm right now, triggering an atomic belief revision to blocked."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_01_audit",
            prompt="Why was route_A superseded?",
            query_sim_time_ms=0,
            after_sample_sequence=1,
            expected_status="audit_retrieved",
            expected_revisions_count=1,
            expected_revisions=[
                {
                    "predecessor_object": "clear",
                    "successor_object": "blocked",
                    "policy_rule": "lidar_observation_route_status",
                }
            ],
            conflict_or_uncertainty_rationale=(
                "Epistemic audit query retrieves revision rationale, predecessor "
                "fact, and policy rule."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_01_cleared",
            prompt="Is the front route clear to move forward?",
            query_sim_time_ms=1000,
            after_sample_sequence=2,
            expected_status="grounded",
            expected_revisions_count=2,
            expected_active_facts=[
                {"subject": "route_A", "predicate": "status_is", "object": "clear"}
            ],
            unexpected_active_facts=[
                {"subject": "route_A", "predicate": "status_is", "object": "blocked"}
            ],
            expected_revisions=[
                {
                    "predecessor_object": "blocked",
                    "successor_object": "clear",
                    "policy_rule": "lidar_observation_route_status",
                }
            ],
            expected_evidence_sample_ids=[s2.sample_id],
            conflict_or_uncertainty_rationale=(
                "Fresh LiDAR observation confirms obstacle is cleared; "
                "route status conflict is resolved to clear with successor belief revision."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_01_map_clear_vs_cart_blocked",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Map-clear vs cart-blocked route, followed by clearance",
            "aliases": {"front route": "route_A", "route_A": "route_A"},
            "initial_facts": [init_fact],
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {"sim_time_ms": 0, "kind": "cart_block", "description": "Cart blocks route_A"},
                {
                    "sim_time_ms": 1000,
                    "kind": "cart_clear",
                    "description": "Cart cleared from route_A",
                },
            ],
            object_ground_truth={"cart_01": [{"sim_time_ms": 0, "active": True}]},
        ),
        presenter_inputs=[
            {"time_ms": 0, "disruption": "cart_block"},
            {"time_ms": 1000, "disruption": "cart_clear"},
        ],
        recorded_samples=[s1, s2],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 2: Visible parcel relocation between known stations
# ---------------------------------------------------------------------------
def scenario_02_visible_parcel_relocation() -> ReplayBundle:
    """Parcel sighted at packing_station, later sighted at inspection_station."""
    run_id = UUID("20000000-0000-0000-0000-000000000002")
    scene = _make_base_scenario(run_id)

    pose1 = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=320.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    pose2 = Pose(
        robot_id="robot_1",
        x_cm=150.0,
        y_cm=360.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )

    p1 = next(e for e in scene.entities if e.entity_id == "parcel_01")
    p1.footprint.x_cm = 80.0
    p1.footprint.y_cm = 350.0

    # Relocate parcel_01 to inspection_station at t=1500ms
    relocate_move = MoveEvent(
        kind="move",
        at_ms=1500,
        sequence=0,
        entity_id="parcel_01",
        to=Point(x_cm=150.0, y_cm=390.0),
        duration_ms=100,
    )
    scene.events = sorted([*scene.events, relocate_move], key=lambda e: (e.at_ms, e.sequence))

    d1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    d2 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )

    s1 = _make_sample(
        sample_id=UUID("20000000-0000-0000-0000-000000000021"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose1,
        target="parcel_01",
        measurements=CameraScan(objects=[d1]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("20000000-0000-0000-0000-000000000022"),
        run_id=run_id,
        seq=2,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose2,
        target="parcel_01",
        measurements=CameraScan(objects=[d2]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_02_current",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            after_sample_sequence=2,
            expected_status="verified",
            expected_location="inspection_station",
            expected_revisions_count=1,
            expected_active_facts=[
                {
                    "subject": "parcel_01",
                    "predicate": "located_at",
                    "object": "inspection_station",
                }
            ],
            unexpected_active_facts=[
                {"subject": "parcel_01", "predicate": "located_at", "object": "packing_station"}
            ],
            expected_revisions=[
                {
                    "predecessor_object": "packing_station",
                    "successor_object": "inspection_station",
                    "policy_rule": "camera_observation_revision",
                }
            ],
            expected_evidence_sample_ids=[s2.sample_id],
            expected_response_evidence_sample_ids=[s2.sample_id],
            conflict_or_uncertainty_rationale=(
                "Current belief is updated to inspection_station based on fresh positive sample "
                "at 2000 ms."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_02_changes",
            prompt="Has parcel_01 moved?",
            query_sim_time_ms=2000,
            after_sample_sequence=2,
            expected_status="verified",
            expected_active_facts=[
                {
                    "subject": "parcel_01",
                    "predicate": "located_at",
                    "object": "inspection_station",
                }
            ],
            conflict_or_uncertainty_rationale=(
                "Movement reported between packing_station and inspection_station with exact "
                "timestamps, accompanied by the mandatory transit caveat."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_02_visible_parcel_relocation",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Visible parcel relocation between known stations",
            "aliases": {"parcel_01": "parcel_01"},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 1500,
                    "kind": "move",
                    "description": "Relocate parcel_01 to inspection",
                }
            ],
            object_ground_truth={
                "parcel_01": [
                    {"sim_time_ms": 1000, "station": "packing_station"},
                    {"sim_time_ms": 2000, "station": "inspection_station"},
                ]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1, s2],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 3: Parcel moves outside view
# ---------------------------------------------------------------------------
def scenario_03_parcel_moves_outside_view() -> ReplayBundle:
    """Camera sees parcel at packing_station; subsequent scan does not see it."""
    run_id = UUID("30000000-0000-0000-0000-000000000003")
    scene = _make_base_scenario(run_id)

    pose1 = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=320.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    scene.robot_pose = pose1
    scene.patrol = [Point(x_cm=80.0, y_cm=320.0), Point(x_cm=80.0, y_cm=350.0)]

    p1 = next(e for e in scene.entities if e.entity_id == "parcel_01")
    p1.footprint.x_cm = 80.0
    p1.footprint.y_cm = 350.0

    remove_move = MoveEvent(
        kind="move",
        at_ms=2000,
        sequence=0,
        entity_id="parcel_01",
        to=Point(x_cm=700.0, y_cm=500.0),
        duration_ms=100,
    )
    scene.events = sorted([*scene.events, remove_move], key=lambda e: (e.at_ms, e.sequence))

    d1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )

    s1 = _make_sample(
        sample_id=UUID("30000000-0000-0000-0000-000000000031"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose1,
        measurements=CameraScan(objects=[d1]).model_dump(mode="json"),
    )
    # Subsequent scan at 3000 ms: empty scan
    s2 = _make_sample(
        sample_id=UUID("30000000-0000-0000-0000-000000000032"),
        run_id=run_id,
        seq=2,
        sim_time_ms=3000,
        capability="camera_detect",
        observer_pose=pose1,
        measurements=CameraScan(objects=[]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_03_current",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=3000,
            expected_status="uncertain",
            expected_location="packing_station",
            expected_response_evidence_sample_ids=[s1.sample_id, s2.sample_id],
            conflict_or_uncertainty_rationale=(
                "Broad camera scan did not detect parcel_01. Location cannot be verified now; "
                "last confirmed sighting at packing_station is retained without guessing."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_03_parcel_moves_outside_view",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Parcel moves outside view",
            "aliases": {"parcel_01": "parcel_01"},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 2000,
                    "kind": "remove_object",
                    "description": "Parcel removed outside camera FOV",
                }
            ],
            object_ground_truth={
                "parcel_01": [
                    {"sim_time_ms": 1000, "station": "packing_station"},
                    {"sim_time_ms": 3000, "station": "removed"},
                ]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1, s2],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 4: Shelf occlusion and later reacquisition
# ---------------------------------------------------------------------------
def scenario_04_shelf_occlusion_and_reacquisition() -> ReplayBundle:
    """Occlusion behind opaque shelf and subsequent reacquisition."""
    run_id = UUID("40000000-0000-0000-0000-000000000004")
    scene = _make_base_scenario(run_id)

    pose = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=320.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    scene.robot_pose = pose
    scene.patrol = [Point(x_cm=80.0, y_cm=320.0), Point(x_cm=80.0, y_cm=350.0)]

    p1 = next(e for e in scene.entities if e.entity_id == "parcel_01")
    p1.footprint.x_cm = 80.0
    p1.footprint.y_cm = 350.0

    # Dimension shelf_01 so it slides cleanly between robot and parcel without boundary collision
    shelf = next(e for e in scene.entities if e.entity_id == "shelf_01")
    shelf.footprint.width_cm = 20.0
    shelf.footprint.height_cm = 4.0
    shelf.footprint.x_cm = 300.0
    shelf.footprint.y_cm = 341.0

    shelf_occlude = MoveEvent(
        kind="move",
        at_ms=1500,
        sequence=0,
        entity_id="shelf_01",
        to=Point(x_cm=80.0, y_cm=341.0),
        duration_ms=100,
    )
    shelf_clear = MoveEvent(
        kind="move",
        at_ms=2500,
        sequence=0,
        entity_id="shelf_01",
        to=Point(x_cm=300.0, y_cm=341.0),
        duration_ms=100,
    )
    scene.events = sorted(
        [*scene.events, shelf_occlude, shelf_clear], key=lambda e: (e.at_ms, e.sequence)
    )

    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )

    s1 = _make_sample(
        sample_id=UUID("40000000-0000-0000-0000-000000000041"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose,
        target="parcel_01",
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("40000000-0000-0000-0000-000000000042"),
        run_id=run_id,
        seq=2,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose,
        target="parcel_01",
        unavailable_reason="target_not_visible",
    )
    s3 = _make_sample(
        sample_id=UUID("40000000-0000-0000-0000-000000000043"),
        run_id=run_id,
        seq=3,
        sim_time_ms=3000,
        capability="camera_detect",
        observer_pose=pose,
        target="parcel_01",
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_04_occluded",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            expected_status="uncertain",
            expected_location="packing_station",
            expected_response_evidence_sample_ids=[s1.sample_id, s2.sample_id],
            conflict_or_uncertainty_rationale=(
                "Targeted camera query reported target_not_visible due to shelf occlusion. "
                "Current belief is uncertain, but earlier sighting at packing_station is preserved."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_04_reacquired",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=3000,
            expected_status="verified",
            expected_location="packing_station",
            expected_response_evidence_sample_ids=[s3.sample_id],
            conflict_or_uncertainty_rationale=(
                "Reacquisition after occlusion restores verified current status."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_04_shelf_occlusion_and_reacquisition",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Shelf occlusion and later reacquisition",
            "aliases": {"parcel_01": "parcel_01"},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 1500,
                    "kind": "shelf_occlude",
                    "description": "Shelf occludes parcel_01",
                },
                {
                    "sim_time_ms": 2500,
                    "kind": "clear_occlusion",
                    "description": "Robot clears occlusion",
                },
            ],
            object_ground_truth={
                "parcel_01": [
                    {"sim_time_ms": 1000, "status": "visible"},
                    {"sim_time_ms": 2000, "status": "occluded"},
                    {"sim_time_ms": 3000, "status": "visible"},
                ]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1, s2, s3],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 5: Two similar parcels / ambiguous mention
# ---------------------------------------------------------------------------
def scenario_05_two_similar_parcels() -> ReplayBundle:
    """Ambiguous reference to multiple similar parcels without identity conflation."""
    run_id = UUID("50000000-0000-0000-0000-000000000005")
    scene = _make_base_scenario(run_id)

    pose = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=350.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    d1 = CameraDetection(
        object_id="box_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )
    d2 = CameraDetection(
        object_id="box_02",
        apparent_color="red",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )

    s1 = _make_sample(
        sample_id=UUID("50000000-0000-0000-0000-000000000051"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose,
        measurements=CameraScan(objects=[d1, d2]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_05_ambiguous",
            prompt="What is the color of the box?",
            query_sim_time_ms=1000,
            expected_status="ambiguous",
            conflict_or_uncertainty_rationale=(
                "Query ambiguously references 'the box' when multiple distinct objects "
                "(box_01, box_02) match. The agent halts and refuses to merge identities."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_05_two_similar_parcels",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Two similar parcels / ambiguous mention",
            "aliases_multi": {"the box": ["box_01", "box_02"]},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[],
            object_ground_truth={
                "box_01": [{"sim_time_ms": 1000, "color": "red"}],
                "box_02": [{"sim_time_ms": 1000, "color": "red"}],
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 6: User expectation vs lighting perception vs maintenance history
# ---------------------------------------------------------------------------
def scenario_06_color_perspectives() -> ReplayBundle:
    """User (Red) vs Egocentric (Brown under yellow light) vs Historical (Blue from bot_02)."""
    run_id = UUID("60000000-0000-0000-0000-000000000006")
    scene = _make_base_scenario(run_id)
    scene.routes = []
    scene.events = []
    scene.initial_light = LightMeasurement(intensity=0.8, color_cast="yellow")

    pose = Pose(
        robot_id="robot_1",
        x_cm=100.0,
        y_cm=100.0,
        direction="north",
        location="room_101",
        frame_of_reference="robot_base",
    )
    scene.robot_pose = pose
    scene.patrol = [Point(x_cm=100.0, y_cm=100.0), Point(x_cm=100.0, y_cm=130.0)]
    scene.entities = [
        SceneEntity(
            entity_id="box_01",
            kind="box",
            location="room_101",
            footprint=Rectangle(x_cm=100.0, y_cm=130.0, width_cm=10.0, height_cm=10.0),
            intrinsic_color="red",
            visible=True,
            solid=True,
            opaque=False,
        )
    ]

    d1 = CameraDetection(
        object_id="box_01",
        apparent_color="brown",
        position=Point(x_cm=100.0, y_cm=130.0),
        frame_of_reference="robot_base",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )

    s1 = _make_sample(
        sample_id=UUID("60000000-0000-0000-0000-000000000061"),
        run_id=run_id,
        seq=1,
        sim_time_ms=0,
        capability="camera_detect",
        observer_pose=pose,
        measurements=CameraScan(objects=[d1]).model_dump(mode="json"),
    )

    hist_fact = FactAssertion(
        subject="box_01",
        predicate="painted_color_is",
        object="blue",
        source_agent="bot_02",
        confidence_score=1.0,
        observed_at=EPOCH,
        context=SpatialContext(location="room_101"),
        evidence={"action": "painted_blue"},
    ).model_dump(mode="json")

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_06_perspectives",
            prompt="Can you see the red box? What color do different perspectives think it is?",
            query_sim_time_ms=0,
            expected_status="perspectives_tracked",
            conflict_or_uncertainty_rationale=(
                "Distinguishes user expectation (red), live egocentric perception "
                "under yellow lighting (brown), and maintenance history (blue) without "
                "conflating them."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_06_color_perspectives",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "User expectation vs lighting perception vs maintenance history",
            "aliases": {"red box": "box_01", "box_01": "box_01"},
            "initial_facts": [hist_fact],
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[],
            object_ground_truth={
                "box_01": [{"intrinsic_color": "red", "perceived": "brown", "history": "blue"}]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 7: Wrong-room/frame sample rejected
# ---------------------------------------------------------------------------
def scenario_07_wrong_context_sample() -> ReplayBundle:
    """Observation tagged with incompatible location/frame cannot revise warehouse belief."""
    run_id = UUID("70000000-0000-0000-0000-000000000007")
    scene = _make_base_scenario(run_id)

    init_fact = FactAssertion(
        subject="parcel_01",
        predicate="located_at",
        object="packing_station",
        source_agent="warehouse_manifest",
        confidence_score=0.9,
        observed_at=EPOCH,
        context=SpatialContext(location="packing_station", frame_of_reference="warehouse_map"),
    ).model_dump(mode="json")

    # Mismatched sample in room_999 with external_frame
    mismatched_pose = Pose(
        robot_id="robot_9",
        x_cm=999.0,
        y_cm=999.0,
        direction="south",
        location="room_999",
        frame_of_reference="external_frame",
    )
    d = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=999.0, y_cm=999.0),
        frame_of_reference="external_frame",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )
    s1 = _make_sample(
        sample_id=UUID("70000000-0000-0000-0000-000000000071"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=mismatched_pose,
        measurements=CameraScan(objects=[d]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_07_mismatched",
            prompt="What was recorded about parcel_01?",
            query_sim_time_ms=1000,
            expected_status="verified",
            expected_location="packing_station",
            conflict_or_uncertainty_rationale=(
                "Observation captured in mismatched room_999 cannot update warehouse beliefs. "
                "Stored warehouse belief at packing_station is preserved without corruption."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_07_wrong_context_sample",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Wrong-room/frame sample rejected",
            "aliases": {"parcel_01": "parcel_01"},
            "initial_facts": [init_fact],
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 1000,
                    "kind": "incompatible_sample",
                    "description": "Foreign sensor sample injected",
                }
            ],
            object_ground_truth={"parcel_01": [{"station": "packing_station"}]},
        ),
        presenter_inputs=[],
        recorded_samples=[s1],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 8: Sensor outage and recovery
# ---------------------------------------------------------------------------
def scenario_08_sensor_outage_and_recovery() -> ReplayBundle:
    """ObservationUnavailable outage and recovery without inventing data."""
    run_id = UUID("80000000-0000-0000-0000-000000000008")
    scene = _make_base_scenario(run_id)

    pose = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=320.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    scene.robot_pose = pose
    scene.patrol = [Point(x_cm=80.0, y_cm=320.0), Point(x_cm=80.0, y_cm=350.0)]

    p1 = next(e for e in scene.entities if e.entity_id == "parcel_01")
    p1.footprint.x_cm = 80.0
    p1.footprint.y_cm = 350.0

    outage = SensorAvailabilityEvent(
        kind="sensor_availability",
        at_ms=2000,
        sequence=0,
        capability="camera_detect",
        available=False,
    )
    recovery = SensorAvailabilityEvent(
        kind="sensor_availability",
        at_ms=3000,
        sequence=0,
        capability="camera_detect",
        available=True,
    )
    scene.events = sorted([*scene.events, outage, recovery], key=lambda e: (e.at_ms, e.sequence))

    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )

    s1 = _make_sample(
        sample_id=UUID("80000000-0000-0000-0000-000000000081"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose,
        target="parcel_01",
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("80000000-0000-0000-0000-000000000082"),
        run_id=run_id,
        seq=2,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose,
        target="parcel_01",
        unavailable_reason="sensor_unavailable",
    )

    s3 = _make_sample(
        sample_id=UUID("80000000-0000-0000-0000-000000000083"),
        run_id=run_id,
        seq=3,
        sim_time_ms=3000,
        capability="camera_detect",
        observer_pose=pose,
        target="parcel_01",
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_08_outage",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            expected_status="uncertain",
            expected_location="packing_station",
            expected_response_evidence_sample_ids=[s1.sample_id, s2.sample_id],
            conflict_or_uncertainty_rationale=(
                "Current camera verification is unavailable due to outage at 2000 ms. "
                "Agent reports uncertain status while retaining last confirmed sighting at 1000 ms."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_08_recovery",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=3000,
            expected_status="verified",
            expected_location="packing_station",
            expected_response_evidence_sample_ids=[s3.sample_id],
            conflict_or_uncertainty_rationale=(
                "A fresh camera sighting after recovery restores the verified location."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_08_sensor_outage_and_recovery",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Sensor outage and recovery",
            "aliases": {"parcel_01": "parcel_01"},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 2000,
                    "kind": "sensor_failure",
                    "description": "Camera hardware fault",
                },
                {
                    "sim_time_ms": 3000,
                    "kind": "sensor_recovery",
                    "description": "Camera restored",
                },
            ],
            object_ground_truth={
                "parcel_01": [{"sim_time_ms": 1000, "station": "packing_station"}]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1, s2, s3],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 9: Delayed older observation
# ---------------------------------------------------------------------------
def scenario_09_delayed_older_observation() -> ReplayBundle:
    """Delayed older sample arrives out-of-order without overwriting newer belief."""
    run_id = UUID("90000000-0000-0000-0000-000000000009")
    scene = _make_base_scenario(run_id)

    pose1 = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=320.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    pose2 = Pose(
        robot_id="robot_1",
        x_cm=150.0,
        y_cm=360.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    scene.robot_pose = pose1
    scene.patrol = [Point(x_cm=80.0, y_cm=320.0), Point(x_cm=80.0, y_cm=350.0)]

    p1 = next(e for e in scene.entities if e.entity_id == "parcel_01")
    p1.footprint.x_cm = 80.0
    p1.footprint.y_cm = 350.0

    relocate_move = MoveEvent(
        kind="move",
        at_ms=1000,
        sequence=0,
        entity_id="parcel_01",
        to=Point(x_cm=150.0, y_cm=390.0),
        duration_ms=100,
    )
    scene.events = sorted([*scene.events, relocate_move], key=lambda e: (e.at_ms, e.sequence))

    d_older = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    d_newer = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )

    # Ingestion order: Newer sample (t=2000 ms) ingested first
    s_newer = _make_sample(
        sample_id=UUID("90000000-0000-0000-0000-000000000091"),
        run_id=run_id,
        seq=1,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose2,
        target="parcel_01",
        measurements=CameraScan(objects=[d_newer]).model_dump(mode="json"),
    )
    # Delayed older sample (t=500 ms) arrives second
    s_older = _make_sample(
        sample_id=UUID("90000000-0000-0000-0000-000000000092"),
        run_id=run_id,
        seq=2,
        sim_time_ms=500,
        capability="camera_detect",
        observer_pose=pose1,
        target="parcel_01",
        measurements=CameraScan(objects=[d_older]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_09_delayed",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            after_sample_sequence=2,
            expected_status="verified",
            expected_location="inspection_station",
            expected_active_facts=[
                {
                    "subject": "parcel_01",
                    "predicate": "located_at",
                    "object": "inspection_station",
                }
            ],
            unexpected_active_facts=[
                {"subject": "parcel_01", "predicate": "located_at", "object": "packing_station"}
            ],
            expected_response_evidence_sample_ids=[s_newer.sample_id],
            conflict_or_uncertainty_rationale=(
                "Delayed older observation from t=500 ms does not overwrite newer active belief "
                "from t=2000 ms. Active belief remains verified at inspection_station."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_09_delayed_older_observation",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Delayed older observation",
            "aliases": {"parcel_01": "parcel_01"},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 2000,
                    "kind": "delayed_packet",
                    "description": "Older sensor sample delivered late",
                }
            ],
            object_ground_truth={
                "parcel_01": [
                    {"sim_time_ms": 500, "station": "packing_station"},
                    {"sim_time_ms": 2000, "station": "inspection_station"},
                ]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s_newer, s_older],
        expected_evaluations=evals,
    )


# ---------------------------------------------------------------------------
# Scenario 10: Movement wholly between samples (anti-interpolation)
# ---------------------------------------------------------------------------
def scenario_10_hidden_movement_between_scans() -> ReplayBundle:
    """Movement occurred wholly between scans; agent strictly refuses to interpolate path."""
    run_id = UUID("a0000000-0000-0000-0000-00000000000a")
    scene = _make_base_scenario(run_id)

    pose1 = Pose(
        robot_id="robot_1",
        x_cm=80.0,
        y_cm=320.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    pose2 = Pose(
        robot_id="robot_1",
        x_cm=150.0,
        y_cm=360.0,
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    scene.robot_pose = pose1
    scene.patrol = [Point(x_cm=80.0, y_cm=320.0), Point(x_cm=80.0, y_cm=350.0)]

    p1 = next(e for e in scene.entities if e.entity_id == "parcel_01")
    p1.footprint.x_cm = 80.0
    p1.footprint.y_cm = 350.0

    unobserved_move = MoveEvent(
        kind="move",
        at_ms=400,
        sequence=0,
        entity_id="parcel_01",
        to=Point(x_cm=150.0, y_cm=390.0),
        duration_ms=100,
    )
    scene.events = sorted([*scene.events, unobserved_move], key=lambda e: (e.at_ms, e.sequence))

    d1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    d2 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )

    s1 = _make_sample(
        sample_id=UUID("a0000000-0000-0000-0000-0000000000a1"),
        run_id=run_id,
        seq=1,
        sim_time_ms=0,
        capability="camera_detect",
        observer_pose=pose1,
        target="parcel_01",
        measurements=CameraScan(objects=[d1]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("a0000000-0000-0000-0000-0000000000a2"),
        run_id=run_id,
        seq=2,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose2,
        target="parcel_01",
        measurements=CameraScan(objects=[d2]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_10_anti_interpolation",
            prompt="Where was parcel_01 at 500 ms?",
            query_sim_time_ms=1000,
            expected_status="verified",
            expected_location="warehouse",
            expected_answer_contains=[
                "captured at 0 ms",
                "No intermediate position is interpolated",
            ],
            expected_response_evidence_sample_ids=[s1.sample_id],
            conflict_or_uncertainty_rationale=(
                "Movement occurred wholly between scans. Agent adheres to strict "
                "anti-interpolation policy: reports latest applicable observation at t=0 ms "
                "and explicitly refuses to interpolate an intermediate physical trajectory."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_10_hidden_movement_between_scans",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={
            "synthetic_evidence": True,
            "title": "Movement between samples with strict anti-interpolation",
            "aliases": {"parcel_01": "parcel_01"},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 400,
                    "kind": "unobserved_transit",
                    "description": "Transit between stations occurred unobserved",
                }
            ],
            object_ground_truth={
                "parcel_01": [
                    {"sim_time_ms": 0, "station": "packing_station"},
                    {"sim_time_ms": 1000, "station": "inspection_station"},
                ]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1, s2],
        expected_evaluations=evals,
    )


ALL_SCENARIO_BUILDERS: dict[str, Callable[[], ReplayBundle]] = {
    "scenario_01_map_clear_vs_cart_blocked": scenario_01_map_clear_vs_cart_blocked,
    "scenario_02_visible_parcel_relocation": scenario_02_visible_parcel_relocation,
    "scenario_03_parcel_moves_outside_view": scenario_03_parcel_moves_outside_view,
    "scenario_04_shelf_occlusion_and_reacquisition": scenario_04_shelf_occlusion_and_reacquisition,
    "scenario_05_two_similar_parcels": scenario_05_two_similar_parcels,
    "scenario_06_color_perspectives": scenario_06_color_perspectives,
    "scenario_07_wrong_context_sample": scenario_07_wrong_context_sample,
    "scenario_08_sensor_outage_and_recovery": scenario_08_sensor_outage_and_recovery,
    "scenario_09_delayed_older_observation": scenario_09_delayed_older_observation,
    "scenario_10_hidden_movement_between_scans": scenario_10_hidden_movement_between_scans,
}
