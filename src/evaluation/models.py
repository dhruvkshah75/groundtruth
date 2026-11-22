"""Contracts and schema models for Phase 2 replay bundles and evaluation logs (P2-12)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.simulation import (
    ObservationSample,
    Point,
    ScenarioDefinition,
    SimulationConfig,
)
from src.procedural.temporal_belief_ingestion import StaticRoute, StationZone


class AudienceTruthSection(BaseModel):
    """Audience and simulator truth: strictly segregated from evidence-only replay."""

    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioDefinition
    events: list[object] = Field(default_factory=list)
    object_ground_truth: dict[str, Any] = Field(default_factory=dict)


class StaticMapConfig(BaseModel):
    """Static warehouse layout used for zone mapping and route corridor resolution."""

    model_config = ConfigDict(extra="forbid")

    zones: list[StationZone] = Field(default_factory=list)
    routes: list[StaticRoute] = Field(default_factory=list)


class ExpectedQueryEvaluation(BaseModel):
    """Specification of an expected temporal query or belief evaluation in a scenario."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    prompt: str
    query_sim_time_ms: int
    after_sample_sequence: int | None = None
    expected_status: str  # e.g. "verified", "last_seen", "uncertain", "blocked", "clear"
    expected_location: str | None = None
    expected_revisions_count: int | None = None
    expected_active_facts: list[dict[str, Any]] = Field(default_factory=list)
    unexpected_active_facts: list[dict[str, Any]] = Field(default_factory=list)
    expected_revisions: list[dict[str, Any]] = Field(default_factory=list)
    expected_evidence_sample_ids: list[UUID] = Field(default_factory=list)
    conflict_or_uncertainty_rationale: str


class ReplayBundle(BaseModel):
    """Versioned, self-contained, JSON-safe run and evaluation artifact."""

    model_config = ConfigDict(extra="forbid")

    bundle_version: int = 1
    schema_version: int = 1
    scenario_id: str
    run_id: UUID
    epoch_utc: datetime
    config: SimulationConfig | None = None
    patrol: list[Point] = Field(default_factory=list)
    presenter_inputs: list[dict[str, Any]] = Field(default_factory=list)
    static_map: StaticMapConfig
    audience_truth: AudienceTruthSection
    recorded_samples: list[ObservationSample]
    expected_evaluations: list[ExpectedQueryEvaluation] = Field(default_factory=list)
    runtime_checkpoint: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_simulation_export(
        cls,
        bundle: Any,
        *,
        scenario_id: str = "exported_run",
        static_map: StaticMapConfig | None = None,
        audience_truth: AudienceTruthSection | None = None,
        expected_evaluations: list[ExpectedQueryEvaluation] | None = None,
    ) -> ReplayBundle:
        """Construct a ReplayBundle from a SimulationExportBundle or raw dict."""
        data = bundle.model_dump(mode="json") if hasattr(bundle, "model_dump") else dict(bundle)

        if static_map is None:
            static_map = StaticMapConfig(zones=[], routes=[])

        if audience_truth is None:
            # If scenario is present in export or checkpoint
            scenario_data = (
                data.get("scenario") or (data.get("runtime_checkpoint") or {}).get("scenario") or {}
            )
            if scenario_data:
                scene = ScenarioDefinition.model_validate(scenario_data)
            else:
                from src.web.simulation_presets import warehouse_preset

                scene = warehouse_preset().model_copy(deep=True)
            audience_truth = AudienceTruthSection(scenario=scene)

        return cls(
            bundle_version=1,
            schema_version=1,
            scenario_id=data.get("scenario_id", scenario_id),
            run_id=UUID(str(data["run_id"])),
            epoch_utc=data["epoch_utc"],
            config=SimulationConfig.model_validate(data["config"]) if data.get("config") else None,
            patrol=[Point.model_validate(p) for p in data.get("patrol", [])],
            presenter_inputs=data.get("presenter_inputs", []),
            static_map=static_map,
            audience_truth=audience_truth,
            recorded_samples=[
                ObservationSample.model_validate(s) for s in data.get("recorded_samples", [])
            ],
            expected_evaluations=expected_evaluations or [],
            runtime_checkpoint=data.get("runtime_checkpoint"),
            metadata=data.get("metadata", {}),
        )


class ScenarioEvaluationLog(BaseModel):
    """Structured report and log for one of the ten evaluation scenarios."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_title: str
    conflict_description: str
    setup: str
    input_timeline: list[str]
    prompt: str
    expected_outcome: dict[str, Any]
    actual_outcome: dict[str, Any]
    recorded_sample_ids: list[UUID]
    recorded_fact_ids: list[UUID] = Field(default_factory=list)
    revision_ids: list[UUID] = Field(default_factory=list)
    policy_applied: str
    passed: bool
    mode: Literal["world_rerun", "evidence_only"]
    failure_reason: str | None = None
