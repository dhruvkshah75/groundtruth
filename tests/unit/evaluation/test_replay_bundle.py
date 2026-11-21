"""Unit tests for ReplayBundle schema, validation, and error handling (P2-12 / WD-05)."""

from uuid import uuid4

import pytest

from src.contracts import BoundingBox, CameraDetection, CameraScan, Point, Pose
from src.evaluation.models import AudienceTruthSection, ReplayBundle
from src.evaluation.replay import validate_bundle
from src.evaluation.scenarios import (
    EPOCH,
    _make_base_scenario,
    _make_default_static_map,
    _make_sample,
)


def _build_valid_minimal_bundle() -> ReplayBundle:
    run_id = uuid4()
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
    sample = _make_sample(
        sample_id=uuid4(),
        run_id=run_id,
        seq=1,
        sim_time_ms=1000,
        capability="camera_detect",
        observer_pose=pose,
        measurements=CameraScan(objects=[det]).model_dump(mode="json"),
    )

    return ReplayBundle(
        bundle_version=1,
        schema_version=1,
        scenario_id="test_minimal",
        run_id=run_id,
        epoch_utc=EPOCH,
        static_map=_make_default_static_map(),
        audience_truth=AudienceTruthSection(scenario=scene),
        recorded_samples=[sample],
    )


def test_valid_bundle_passes_validation() -> None:
    bundle = _build_valid_minimal_bundle()
    validate_bundle(bundle)


def test_unsupported_bundle_version_rejected() -> None:
    bundle = _build_valid_minimal_bundle()
    invalid_bundle = bundle.model_copy(update={"bundle_version": 2})
    with pytest.raises(ValueError, match="Unsupported bundle version: 2"):
        validate_bundle(invalid_bundle)


def test_unsupported_schema_version_rejected() -> None:
    bundle = _build_valid_minimal_bundle()
    invalid_bundle = bundle.model_copy(update={"schema_version": 99})
    with pytest.raises(ValueError, match="Unsupported schema version: 99"):
        validate_bundle(invalid_bundle)


def test_empty_scenario_id_rejected() -> None:
    bundle = _build_valid_minimal_bundle()
    invalid_bundle = bundle.model_copy(update={"scenario_id": ""})
    with pytest.raises(ValueError, match="scenario_id must not be empty"):
        validate_bundle(invalid_bundle)


def test_duplicate_sample_id_rejected() -> None:
    bundle = _build_valid_minimal_bundle()
    sample = bundle.recorded_samples[0]
    duplicate_sample = sample.model_copy(update={"sample_sequence": 2})
    invalid_bundle = bundle.model_copy(update={"recorded_samples": [sample, duplicate_sample]})
    with pytest.raises(ValueError, match="Duplicate sample_id detected"):
        validate_bundle(invalid_bundle)


def test_mismatched_sample_run_id_rejected() -> None:
    bundle = _build_valid_minimal_bundle()
    sample = bundle.recorded_samples[0]
    alien_sample = sample.model_copy(update={"run_id": uuid4()})
    invalid_bundle = bundle.model_copy(update={"recorded_samples": [alien_sample]})
    with pytest.raises(ValueError, match="does not match bundle"):
        validate_bundle(invalid_bundle)


def test_audience_truth_segregation() -> None:
    bundle = _build_valid_minimal_bundle()
    # Audience truth exists in audience_truth section
    assert bundle.audience_truth.scenario is not None
    # Samples in recorded_samples contain no audience_truth reference
    for s in bundle.recorded_samples:
        assert not hasattr(s, "audience_truth")
