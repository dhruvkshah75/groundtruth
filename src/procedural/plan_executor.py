"""Executes validated plans against Tier 1 and Tier 3."""

from typing import Protocol
from uuid import UUID

from src.contracts import (
    AuditTrail,
    FactQuery,
    ObservationRequest,
    ObservationUnavailable,
    SensorObservation,
    StoredFact,
)
from src.procedural.execution_plan import ExecutionPlan
from src.procedural.execution_results import (
    AuditExecutionResult,
    MemoryExecutionResult,
    ObservationExecutionResult,
    PlanExecutionResult,
)


class MemoryRepositoryInterface(Protocol):
    def query_facts(self, query: FactQuery) -> list[StoredFact]: ...
    def get_audit_chain(self, fact_id: UUID) -> AuditTrail: ...


class EnvironmentInterface(Protocol):
    def observe(
        self, request: ObservationRequest
    ) -> SensorObservation | ObservationUnavailable: ...


class PlanExecutor:
    """Executes the deterministic operations in an ExecutionPlan."""

    def __init__(
        self, memory_repository: MemoryRepositoryInterface, environment: EnvironmentInterface
    ):
        self._memory = memory_repository
        self._environment = environment

    def execute(self, plan: ExecutionPlan) -> PlanExecutionResult:
        """Execute all memory, observation, and audit operations in the plan."""
        memory_results: list[MemoryExecutionResult] = []
        observation_results: list[ObservationExecutionResult] = []
        audit_results: list[AuditExecutionResult] = []

        # 1. Execute memory queries
        for idx, mem_op in enumerate(plan.memory_operations):
            facts = self._memory.query_facts(mem_op.query)
            memory_results.append(
                MemoryExecutionResult(operation_index=idx, purpose=mem_op.purpose, facts=facts)
            )

        # 2. Execute observations
        for idx, obs_op in enumerate(plan.observation_operations):
            result = self._environment.observe(obs_op.request)
            observation_results.append(
                ObservationExecutionResult(
                    operation_index=idx, purpose=obs_op.purpose, result=result
                )
            )

        # 3. Execute audit lookups
        for idx, audit_op in enumerate(plan.audit_operations):
            target_mem_op_idx = audit_op.fact_ids_from_memory_operation
            target_facts = memory_results[target_mem_op_idx].facts

            trails: dict[UUID, AuditTrail] = {}
            for fact in target_facts:
                trails[fact.fact_id] = self._memory.get_audit_chain(fact.fact_id)

            audit_results.append(
                AuditExecutionResult(operation_index=idx, purpose=audit_op.purpose, trails=trails)
            )

        approved_operations: list[str] = []
        for mem_op in plan.memory_operations:
            approved_operations.append(f"memory_query: {mem_op.purpose}")
        for obs_op in plan.observation_operations:
            approved_operations.append(f"observe: {obs_op.request.capability}")
        for audit_op in plan.audit_operations:
            approved_operations.append(f"audit_lookup: {audit_op.purpose}")

        # 4. ActionPlaceholder is ignored as per instructions.
        return PlanExecutionResult(
            plan_verifiable=plan.current_state_verifiable,
            blocking_reasons=plan.blocking_reasons,
            plan_reason=plan.plan_reason,
            memory_results=memory_results,
            observation_results=observation_results,
            audit_results=audit_results,
            approved_operations=approved_operations,
        )
