"""Typed data for work that a later executor may perform."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.contracts import FactQuery, IntentRequest, ObservationRequest


class MemoryQueryOperation(BaseModel):
    """One approved request for facts from Tier 1.

    Fields:
        query: The exact fact query a later executor may run.
        purpose: Why that query is needed for this plan.
    """

    model_config = ConfigDict(extra="forbid")

    query: FactQuery
    purpose: str = Field(min_length=1)


class ObservationOperation(BaseModel):
    """One approved request for a Tier 3 observation.

    Fields:
        request: The exact sensor request a later executor may run.
        purpose: Why that observation is needed for this plan.
    """

    model_config = ConfigDict(extra="forbid")

    request: ObservationRequest
    purpose: str = Field(min_length=1)


class AuditChainOperation(BaseModel):
    """One approved request for an audit history.

    Fields:
        entity_id: The canonical entity whose matching facts need audit history.
        fact_ids_from_memory_operation: The memory-operation index whose
            every returned fact needs an audit lookup.
        purpose: Why the history is needed for this plan.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    fact_ids_from_memory_operation: int = Field(ge=0)
    purpose: str = Field(min_length=1)


class ActionPlaceholder(BaseModel):
    """A future action that still needs a safety check before execution.

    Fields:
        action_name: The allowlisted action name.
        requires_safety_precheck: Always ``True`` so a later executor checks
            safety.
    """

    model_config = ConfigDict(extra="forbid")

    action_name: str = Field(min_length=1)
    requires_safety_precheck: Literal[True] = True


class ExecutionPlan(BaseModel):
    """A validated, non-executable list of required evidence operations.

    Fields:
        intent: The validated user request this plan serves.
        resolved_entity_ids: Canonical entity IDs used by the operations.
        memory_operations: Approved Tier 1 fact queries.
        observation_operations: Approved Tier 3 sensor requests.
        audit_operations: Approved audit-history requests.
        action_placeholder: A future action that is not executed here.
        current_state_verifiable: Whether the required live evidence exists.
        blocking_reasons: Reasons a current claim may be unverifiable.
        plan_reason: Short explanation of why these operations are required.
    """

    model_config = ConfigDict(extra="forbid")

    intent: IntentRequest
    resolved_entity_ids: list[str] = Field(default_factory=list)
    memory_operations: list[MemoryQueryOperation] = Field(default_factory=list)
    observation_operations: list[ObservationOperation] = Field(default_factory=list)
    audit_operations: list[AuditChainOperation] = Field(default_factory=list)
    action_placeholder: ActionPlaceholder | None = None
    current_state_verifiable: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    plan_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def audit_operations_reference_memory_results(self) -> "ExecutionPlan":
        """Check each audit operation points to an existing memory lookup.

        Returns:
            The same validated plan.
        """
        for operation in self.audit_operations:
            if operation.fact_ids_from_memory_operation >= len(self.memory_operations):
                raise ValueError("audit operation must reference an existing memory operation")
            query = self.memory_operations[operation.fact_ids_from_memory_operation].query
            if query.subject != operation.entity_id:
                raise ValueError("audit operation entity must match its memory query subject")
        return self
