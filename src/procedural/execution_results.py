"""Typed results produced by executing a plan."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import (
    AuditTrail,
    IntentRequest,
    ObservationUnavailable,
    SensorObservation,
    StoredFact,
    StoredObservationSample,
)


class MemoryExecutionResult(BaseModel):
    """Result of one memory query operation."""

    model_config = ConfigDict(extra="forbid")
    operation_index: int = Field(ge=0)
    purpose: str
    facts: list[StoredFact]


class ObservationExecutionResult(BaseModel):
    """Result of one observation operation."""

    model_config = ConfigDict(extra="forbid")
    operation_index: int = Field(ge=0)
    purpose: str
    result: SensorObservation | ObservationUnavailable


class TemporalObservationExecutionResult(BaseModel):
    """Result of one temporal observation ledger query."""

    model_config = ConfigDict(extra="forbid")
    operation_index: int = Field(ge=0)
    purpose: str
    mode: str
    run_id: UUID | None = None
    entity_id: str | None = None
    capability: str | None = None
    at_sim_time_ms: int | None = None
    samples: list[StoredObservationSample] = Field(default_factory=list)


class AuditExecutionResult(BaseModel):
    """Result of one audit lookup operation."""

    model_config = ConfigDict(extra="forbid")
    operation_index: int = Field(ge=0)
    purpose: str
    trails: dict[UUID, AuditTrail]


class PlanExecutionResult(BaseModel):
    """Results collected from executing a validated plan."""

    model_config = ConfigDict(extra="forbid")
    intent: IntentRequest | None = None
    plan_verifiable: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    plan_reason: str
    memory_results: list[MemoryExecutionResult] = Field(default_factory=list)
    observation_results: list[ObservationExecutionResult] = Field(default_factory=list)
    temporal_results: list[TemporalObservationExecutionResult] = Field(default_factory=list)
    audit_results: list[AuditExecutionResult] = Field(default_factory=list)
    approved_operations: list[str] = Field(default_factory=list)
