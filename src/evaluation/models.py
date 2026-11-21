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
    expected_status: str  # e.g. "verified", "last_seen", "uncertain", "blocked", "clear"
    expected_location: str | None = None
    expected_revisions_count: int | None = None
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
