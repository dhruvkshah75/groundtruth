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
    ScenarioDefinition,
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
    return warehouse_preset()


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
                zone_id="inspection_station",
                location="warehouse",
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
    """Create a validated ObservationSample envelope."""
    captured_at = EPOCH + timedelta(milliseconds=sim_time_ms)
    parameters = {"direction": "front"} if capability == "lidar_scan" else {}
    request = ObservationRequest(capability=capability, target=target, parameters=parameters)

    if unavailable_reason is not None:
        result = ObservationUnavailable(
            capability=capability,
            reason=unavailable_reason,
            reported_at=captured_at,
            message="Observation unavailable.",
        )
    else:
        context = SpatialContext(
            observer=observer_pose.robot_id,
            location=observer_pose.location,
            frame_of_reference=observer_pose.frame_of_reference,
            world_version=world_version,
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
        x_cm=0.0,
        y_cm=0.0,
        direction="north",
        location="room_101",
        frame_of_reference="robot_base",
    )

    # Initial static map fact asserting route_A is clear
    init_fact = FactAssertion(
        subject="route_A",
        predicate="status_is",
        object="clear",
        source_agent="static_map",
        confidence_score=0.95,
        observed_at=EPOCH,
        context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
    ).model_dump(mode="json")

    # Sample 1 (t=0): Front LiDAR blocked by cart at 12 cm
    s1 = _make_sample(
        sample_id=UUID("10000000-0000-0000-0000-000000000011"),
        run_id=run_id,
        seq=1,
        sim_time_ms=0,
        capability="lidar_scan",
        observer_pose=pose,
        measurements={
            "nearest_distance_cm": 12.0,
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
            expected_status="grounded_conflict_resolved",
            conflict_or_uncertainty_rationale=(
                "Static map indicates route_A is clear, but live front LiDAR detects obstacle "
                "at 12.0 cm right now, triggering an atomic belief revision to blocked."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_01_audit",
            prompt="Why was route_A superseded?",
            query_sim_time_ms=0,
            expected_status="audit_retrieved",
            conflict_or_uncertainty_rationale=(
                "Epistemic audit query retrieves revision rationale, predecessor "
                "fact, and policy rule."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_01_cleared",
            prompt="Is the front route clear to move forward?",
            query_sim_time_ms=1000,
            expected_status="grounded_conflict_resolved",
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
            "title": "Map-clear vs cart-blocked route, followed by clearance",
            "aliases": {"front route": "route_A", "route_A": "route_A"},
            "initial_facts": [init_fact],
            "mock_world": {
                "robot_location": "room_101",
                "robot_frame": "robot_base",
            },
            "world_obstacles_timeline": {
                0: [
                    {
                        "obstacle_id": "cart_01",
                        "x_cm": 0.0,
                        "y_cm": 12.0,
                        "location": "room_101",
                        "active": True,
                    }
                ],
                1000: [],
            },
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
        y_cm=350.0,
        direction="north",
        location="packing_station",
        frame_of_reference="warehouse_map",
    )
    pose2 = Pose(
        robot_id="robot_1",
        x_cm=150.0,
        y_cm=390.0,
        direction="north",
        location="inspection_station",
        frame_of_reference="warehouse_map",
    )

    d1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )
    d2 = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )

    s1 = _make_sample(
        sample_id=UUID("20000000-0000-0000-0000-000000000021"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose1,
        measurements=CameraScan(objects=[d1]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("20000000-0000-0000-0000-000000000022"),
        run_id=run_id,
        seq=2,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose2,
        measurements=CameraScan(objects=[d2]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_02_current",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            expected_status="verified",
            expected_location="inspection_station",
            conflict_or_uncertainty_rationale=(
                "Current belief is updated to inspection_station based on fresh positive sample "
                "at 2000 ms."
            ),
        ),
        ExpectedQueryEvaluation(
            query_id="scen_02_changes",
            prompt="Has parcel_01 moved?",
            query_sim_time_ms=2000,
            expected_status="verified",
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
            "title": "Visible parcel relocation between known stations",
            "aliases": {"parcel_01": "parcel_01"},
        },
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 1500,
                    "kind": "teleport_object",
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
        y_cm=350.0,
        direction="north",
        location="packing_station",
        frame_of_reference="warehouse_map",
    )
    d1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
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
        metadata={"title": "Parcel moves outside view", "aliases": {"parcel_01": "parcel_01"}},
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
        y_cm=350.0,
        direction="north",
        location="packing_station",
        frame_of_reference="warehouse_map",
    )
    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )

    s1 = _make_sample(
        sample_id=UUID("40000000-0000-0000-0000-000000000041"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose,
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("40000000-0000-0000-0000-000000000042"),
        run_id=run_id,
        seq=2,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose,
        unavailable_reason="target_not_visible",
        target="parcel_01",
    )
    s3 = _make_sample(
        sample_id=UUID("40000000-0000-0000-0000-000000000043"),
        run_id=run_id,
        seq=3,
        sim_time_ms=3000,
        capability="camera_detect",
        observer_pose=pose,
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_04_occluded",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            expected_status="uncertain",
            expected_location="packing_station",
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

    pose = Pose(
        robot_id="robot_1",
        x_cm=0.0,
        y_cm=0.0,
        direction="north",
        location="room_101",
        frame_of_reference="robot_base",
    )

    d1 = CameraDetection(
        object_id="box_01",
        apparent_color="brown",
        position=Point(x_cm=10.0, y_cm=10.0),
        frame_of_reference="robot_base",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
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
            "title": "User expectation vs lighting perception vs maintenance history",
            "aliases": {"red box": "box_01", "box_01": "box_01"},
            "initial_facts": [hist_fact],
            "mock_world": {
                "light_color_cast": "yellow",
                "objects": [
                    {
                        "object_id": "box_01",
                        "label": "box",
                        "intrinsic_color": "red",
                        "location": "room_101",
                        "x_cm": 10.0,
                        "y_cm": 10.0,
                    }
                ],
            },
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
        y_cm=350.0,
        direction="north",
        location="packing_station",
        frame_of_reference="warehouse_map",
    )
    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )

    s1 = _make_sample(
        sample_id=UUID("80000000-0000-0000-0000-000000000081"),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose,
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("80000000-0000-0000-0000-000000000082"),
        run_id=run_id,
        seq=2,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose,
        unavailable_reason="sensor_unavailable",
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_08_outage",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            expected_status="uncertain",
            expected_location="packing_station",
            conflict_or_uncertainty_rationale=(
                "Current camera verification is unavailable due to outage at 2000 ms. "
                "Agent reports uncertain status while retaining last confirmed sighting at 1000 ms."
            ),
        ),
    ]

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="scenario_08_sensor_outage_and_recovery",
        run_id=run_id,
        epoch_utc=EPOCH,
        metadata={"title": "Sensor outage and recovery", "aliases": {"parcel_01": "parcel_01"}},
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(
            scenario=scene,
            events=[
                {
                    "sim_time_ms": 2000,
                    "kind": "sensor_failure",
                    "description": "Camera hardware fault",
                }
            ],
            object_ground_truth={
                "parcel_01": [{"sim_time_ms": 1000, "station": "packing_station"}]
            },
        ),
        presenter_inputs=[],
        recorded_samples=[s1, s2],
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
        y_cm=350.0,
        direction="north",
        location="packing_station",
        frame_of_reference="warehouse_map",
    )
    pose2 = Pose(
        robot_id="robot_1",
        x_cm=150.0,
        y_cm=390.0,
        direction="north",
        location="inspection_station",
        frame_of_reference="warehouse_map",
    )

    d_older = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )
    d_newer = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )

    # Ingestion order: Newer sample (t=2000 ms) ingested first
    s_newer = _make_sample(
        sample_id=UUID("90000000-0000-0000-0000-000000000091"),
        run_id=run_id,
        seq=1,
        sim_time_ms=2000,
        capability="camera_detect",
        observer_pose=pose2,
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
        measurements=CameraScan(objects=[d_older]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_09_delayed",
            prompt="Where is parcel_01 now?",
            query_sim_time_ms=2000,
            expected_status="verified",
            expected_location="inspection_station",
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
        metadata={"title": "Delayed older observation", "aliases": {"parcel_01": "parcel_01"}},
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
        y_cm=350.0,
        direction="north",
        location="packing_station",
        frame_of_reference="warehouse_map",
    )
    pose2 = Pose(
        robot_id="robot_1",
        x_cm=150.0,
        y_cm=390.0,
        direction="north",
        location="inspection_station",
        frame_of_reference="warehouse_map",
    )

    d1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )
    d2 = CameraDetection(
        object_id="parcel_01",
        apparent_color="cardboard_brown",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=30.0, height_cm=30.0),
    )

    s1 = _make_sample(
        sample_id=UUID("a0000000-0000-0000-0000-0000000000a1"),
        run_id=run_id,
        seq=1,
        sim_time_ms=0,
        capability="camera_detect",
        observer_pose=pose1,
        measurements=CameraScan(objects=[d1]).model_dump(mode="json"),
    )
    s2 = _make_sample(
        sample_id=UUID("a0000000-0000-0000-0000-0000000000a2"),
        run_id=run_id,
        seq=2,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose2,
        measurements=CameraScan(objects=[d2]).model_dump(mode="json"),
    )

    evals = [
        ExpectedQueryEvaluation(
            query_id="scen_10_anti_interpolation",
            prompt="Where was parcel_01 at 500 ms?",
            query_sim_time_ms=1000,
            expected_status="verified",
            expected_location="packing_station",
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
