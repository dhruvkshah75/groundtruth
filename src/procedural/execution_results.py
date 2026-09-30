"""Typed results produced by executing a plan."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import AuditTrail, ObservationUnavailable, SensorObservation, StoredFact


class MemoryExecutionResult(BaseModel):
    """Result of one memory query operation."""

    model_config = ConfigDict(extra="forbid")
    operation_index: int = Field(ge=0)
    facts: list[StoredFact]


class ObservationExecutionResult(BaseModel):
    """Result of one observation operation."""

    model_config = ConfigDict(extra="forbid")
    operation_index: int = Field(ge=0)
    result: SensorObservation | ObservationUnavailable


class AuditExecutionResult(BaseModel):
    """Result of one audit lookup operation."""

    model_config = ConfigDict(extra="forbid")
    operation_index: int = Field(ge=0)
    trails: dict[UUID, AuditTrail]


class PlanExecutionResult(BaseModel):
    """Results collected from executing a validated plan."""

    model_config = ConfigDict(extra="forbid")
    memory_results: list[MemoryExecutionResult] = Field(default_factory=list)
    observation_results: list[ObservationExecutionResult] = Field(default_factory=list)
    audit_results: list[AuditExecutionResult] = Field(default_factory=list)
