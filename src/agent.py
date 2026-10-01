"""Unified cognitive agent entrypoint for GroundTruth.

Integrates:
- Tier 1 (Declarative SQLite evidence ledger & NetworkX active-belief graph)
- Tier 2 (Procedural intent planning & epistemic evaluation)
- Tier 3 (Sensorimotor mock environment)
into a coherent, auditable ReAct cycle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from src.composition import CompositionService
from src.contracts import AuditEvent, AuditTrail, FactQuery, SensorObservation, StoredFact
from src.declarative.active_graph import ActiveBeliefGraph, GraphSyncedRepository
from src.declarative.memory_repository import MemoryRepository, RevisionOutcome
from src.procedural.capability_validator import CapabilityValidator
from src.procedural.epistemic_evaluator import EpistemicEvaluation, EpistemicEvaluator
from src.procedural.execution_results import PlanExecutionResult
from src.procedural.intent_coverage_reviewer import IntentCoverageReviewer
from src.procedural.intent_planner import IntentPlanner
from src.procedural.intent_provider import IntentProvider, IntentProviderUnavailableError
from src.procedural.plan_builder import PlanBuilder
from src.procedural.rule_based_provider import RuleBasedIntentProvider
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import RobotState, WorldState

LOGGER = logging.getLogger("groundtruth.agent")


def validate_grounded_response(
    candidate: str,
    eval_outcome: EpistemicEvaluation,
) -> bool:
    """Accept only the deterministic rendering of the verified evaluation.

    Free-form text cannot be reliably checked for every contradiction or unsupported
    claim with keyword rules. Normalize harmless formatting differences, then fail
    closed for any semantic change. The verified Python rendering remains authoritative.
    """
    if not candidate or not eval_outcome.explanation:
        return False

    def normalized(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()

    return normalized(candidate) == normalized(eval_outcome.explanation)


@dataclass
class ReActTrace:
    """Inspecting the bounded ReAct cycle."""

    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    approved_operations: list[str]
    execution_summary: dict[str, Any] | None = None
    explanation_source: str = "llm"  # "llm", "deterministic_fallback", or "rule_based"
    model: str | None = None
    operations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentResponse:
    """Comprehensive structured outcome returned by GroundedAgent."""

    question: str
    answer: str
    status: str
    perspectives: dict[str, str] | None = None
    revisions: list[RevisionOutcome] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)
    audit_trails: list[AuditTrail] = field(default_factory=list)
    active_facts: list[StoredFact] = field(default_factory=list)
    sensor_telemetry: list[dict[str, Any]] = field(default_factory=list)
    graph: nx.MultiDiGraph = field(default_factory=nx.MultiDiGraph)
    plan_verifiable: bool | None = None
    plan_reason: str | None = None
    react_trace: ReActTrace | None = None


class GroundedAgent:
    """Three-tier cognitive agent that maintains grounded beliefs and provenance."""

    def __init__(
        self,
        composition: CompositionService,
        memory: MemoryRepository,
        graph: ActiveBeliefGraph,
        environment: MockEnvironment,
        evaluator: EpistemicEvaluator | None = None,
        provider: IntentProvider | None = None,
    ) -> None:
        self._composition = composition
        self._memory = memory
        self._graph = graph
        self._environment = environment
        self._evaluator = evaluator or EpistemicEvaluator()
        self._provider = provider

    @classmethod
    def create(
        cls,
        memory: MemoryRepository | None = None,
        environment: MockEnvironment | None = None,
        provider: IntentProvider | None = None,
    ) -> GroundedAgent:
        """Convenience factory to instantiate a GroundedAgent with standard wiring."""
        mem = memory or MemoryRepository(":memory:")
        abg = ActiveBeliefGraph(mem)
        synced_repo = GraphSyncedRepository(mem, abg)

        if environment is None:
            world = WorldState(
                robot=RobotState(
                    robot_id="robot_1",
                    x_cm=0.0,
                    y_cm=0.0,
                    direction="north",
                    location="room_101",
                    frame_of_reference="robot_base",
                )
            )
            env = MockEnvironment(world=world)
        else:
            env = environment

        intent_prov = provider or RuleBasedIntentProvider()
        validator = CapabilityValidator(env.get_capabilities())
        reviewer = IntentCoverageReviewer(validator)
        planner = IntentPlanner(intent_prov, reviewer)
        builder = PlanBuilder(synced_repo, validator)
        composition = CompositionService(planner, builder, synced_repo, env)
        evaluator = EpistemicEvaluator()

        return cls(
            composition=composition,
            memory=mem,
            graph=abg,
            environment=env,
            evaluator=evaluator,
            provider=intent_prov,
        )

    @property
    def memory(self) -> MemoryRepository:
        """Direct access to the underlying Tier 1 memory ledger."""
        return self._memory

    @property
    def environment(self) -> MockEnvironment:
        """Direct access to the underlying Tier 3 environment."""
        return self._environment

    @property
    def graph(self) -> ActiveBeliefGraph:
        """Direct access to the Tier 1 NetworkX graph projection."""
        return self._graph

    @property
    def provider(self) -> IntentProvider | None:
        """Direct access to the underlying intent provider."""
        return self._provider

    def close(self) -> None:
        """Release the owned SQLite connection when the session is discarded."""
        self._memory.remove_write_listener(self._graph.invalidate)
        self._memory.close()

    def ask(self, question: str) -> AgentResponse:
        """Execute one complete grounded query cycle: plan -> execute -> evaluate -> synthesize."""
        # 1. Execute plan via composition boundary
        plan_result = self._composition.process_question(question)

        # Propagate live provider failures immediately to the API layer
        fallback = getattr(plan_result, "fallback", None)
        if fallback is not None and fallback.category == "provider_unavailable":
            last_err = getattr(self._provider, "last_error", None) or fallback.reason
            raise IntentProviderUnavailableError(f"Live intent provider failed: {last_err}")

        # 2. Epistemically evaluate against memory and observations
        eval_outcome: EpistemicEvaluation = self._evaluator.evaluate(
            plan_result=plan_result,
            user_question=question,
            memory_repo=self._memory,
        )

        # 3. Pull current active projection and facts
        current_graph = self._graph.get_graph()
        active_facts = self._memory.query_facts(FactQuery(active_only=True))

        plan_verifiable = getattr(plan_result, "plan_verifiable", None)

        # 4. Synthesize final answer via ReAct loop when provider supports it
        final_answer = eval_outcome.explanation
        explanation_source = "rule_based"
        tool_name = "intent_evaluation"
        tool_call_id = "deterministic_0"
        tool_args: dict[str, Any] = {}
        model_name = None

        if self._provider is not None:
            tool_name = getattr(self._provider, "last_tool_name", "") or "intent_evaluation"
            tool_call_id = getattr(self._provider, "last_tool_call_id", "") or "deterministic_0"
            tool_args = getattr(self._provider, "last_tool_args", {}) or {}
            model_name = getattr(self._provider, "model_name", None)

            if hasattr(self._provider, "generate_grounded_explanation"):
                tool_result = {
                    "status": eval_outcome.status,
                    "perspectives": eval_outcome.perspectives,
                    "revisions": [
                        {
                            "old_fact_id": str(r.audit_event.input_fact_ids[0])
                            if r.audit_event.input_fact_ids
                            else None,
                            "new_fact_id": str(r.successor.fact_id),
                            "reason": r.audit_event.reason,
                            "new_fact": (
                                f"{r.successor.subject} {r.successor.predicate} "
                                f"{r.successor.object}"
                            ),
                        }
                        for r in eval_outcome.revisions
                    ],
                    "sensor_observations": eval_outcome.sensor_telemetry,
                    "active_memory_facts": [
                        f"{f.subject} {f.predicate} {f.object} (source: {f.source_agent})"
                        for f in eval_outcome.memory_facts
                    ],
                    "deterministic_evaluation_summary": eval_outcome.explanation,
                }
                try:
                    grounded_text = self._provider.generate_grounded_explanation(
                        user_question=question,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        tool_result=tool_result,
                        fallback_explanation=eval_outcome.explanation,
                    )
                    if (
                        grounded_text
                        and grounded_text.strip()
                        and validate_grounded_response(grounded_text, eval_outcome)
                    ):
                        final_answer = grounded_text.strip()
                        explanation_source = "llm"
                    else:
                        LOGGER.warning(
                            "LLM grounded explanation failed consistency guard or was empty; "
                            "safely using deterministic evaluation outcome"
                        )
                        final_answer = eval_outcome.explanation
                        explanation_source = "deterministic_fallback"
                except IntentProviderUnavailableError:
                    raise
                except Exception as exc:
                    LOGGER.warning("LLM explanation call failed (%s); using fallback", exc)
                    final_answer = eval_outcome.explanation
                    explanation_source = "deterministic_fallback"

        approved_ops = getattr(plan_result, "approved_operations", [])
        execution_summary = {
            "memory_queries_count": len(getattr(plan_result, "memory_results", [])),
            "observations_count": len(getattr(plan_result, "observation_results", [])),
            "audit_lookups_count": len(getattr(plan_result, "audit_results", [])),
            "plan_verifiable": plan_verifiable,
        }

        # Build detailed operations trace with actual returned values and evidence IDs
        operations_trace: list[dict[str, Any]] = []
        if isinstance(plan_result, PlanExecutionResult):
            for mem_res in plan_result.memory_results:
                fact_ids = [str(f.fact_id) for f in mem_res.facts]
                returned_facts = [
                    {
                        "fact_id": str(f.fact_id),
                        "subject": f.subject,
                        "predicate": f.predicate,
                        "object": f.object,
                        "source": f.source_agent,
                    }
                    for f in mem_res.facts
                ]
                summary = (
                    "; ".join(
                        f"{f.subject} {f.predicate} {f.object} ({f.source_agent})"
                        for f in mem_res.facts
                    )
                    if mem_res.facts
                    else "No active facts"
                )
                operations_trace.append(
                    {
                        "name": "query_active_facts",
                        "purpose": mem_res.purpose,
                        "ran": True,
                        "status": "completed",
                        "evidence_ids": fact_ids,
                        "returned_values": returned_facts,
                        "summary": summary,
                    }
                )

            for obs_res in plan_result.observation_results:
                obs = obs_res.result
                if isinstance(obs, SensorObservation):
                    obs_id = [str(obs.observation_id)]
                    measurements = dict(obs.measurements)
                    meas_str = ", ".join(f"{k}={v}" for k, v in measurements.items())
                    summary = f"{obs.sensor}: {meas_str}"
                    status = "completed"
                    op_name = obs.sensor
                else:
                    obs_id = []
                    measurements = {"error": obs.message, "reason": obs.reason}
                    summary = f"{obs.capability} unavailable: {obs.message}"
                    status = obs.reason
                    op_name = obs.capability

                operations_trace.append(
                    {
                        "name": op_name,
                        "purpose": obs_res.purpose,
                        "ran": True,
                        "status": status,
                        "evidence_ids": obs_id,
                        "returned_values": measurements,
                        "summary": summary,
                    }
                )

            for audit_res in plan_result.audit_results:
                audit_ids = [str(eid) for eid in audit_res.trails.keys()]
                operations_trace.append(
                    {
                        "name": "audit_lookup",
                        "purpose": audit_res.purpose,
                        "ran": True,
                        "status": "completed",
                        "evidence_ids": audit_ids,
                        "returned_values": {
                            str(k): [str(e.event_id) for e in trail.events]
                            for k, trail in audit_res.trails.items()
                        },
                        "summary": f"Retrieved {len(audit_res.trails)} audit trail(s)",
                    }
                )

        react_trace = ReActTrace(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=tool_args,
            approved_operations=list(approved_ops),
            execution_summary=execution_summary,
            explanation_source=explanation_source,
            model=model_name,
            operations=operations_trace,
        )

        return AgentResponse(
            question=question,
            answer=final_answer,
            status=eval_outcome.status,
            perspectives=eval_outcome.perspectives,
            revisions=eval_outcome.revisions,
            audit_events=eval_outcome.audit_events,
            audit_trails=eval_outcome.audit_trails,
            active_facts=active_facts,
            sensor_telemetry=eval_outcome.sensor_telemetry,
            graph=current_graph,
            plan_verifiable=plan_verifiable,
            plan_reason=_plan_reason(plan_result),
            react_trace=react_trace,
        )


def _plan_reason(plan_result: object) -> str | None:
    """Expose the deterministic plan rationale or a structured planning failure."""
    reason = getattr(plan_result, "plan_reason", None)
    if reason:
        return str(reason)
    fallback = getattr(plan_result, "fallback", None)
    return str(getattr(fallback, "reason", "")) or None
