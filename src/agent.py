"""Unified cognitive agent entrypoint for GroundTruth.

Integrates:
- Tier 1 (Declarative SQLite evidence ledger & NetworkX active-belief graph)
- Tier 2 (Procedural intent planning & epistemic evaluation)
- Tier 3 (Sensorimotor mock environment)
into a coherent, auditable ReAct cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from src.composition import CompositionService
from src.contracts import AuditEvent, StoredFact
from src.declarative.active_graph import ActiveBeliefGraph, GraphSyncedRepository
from src.declarative.memory_repository import MemoryRepository, RevisionOutcome
from src.procedural.capability_validator import CapabilityValidator
from src.procedural.epistemic_evaluator import EpistemicEvaluation, EpistemicEvaluator
from src.procedural.intent_coverage_reviewer import IntentCoverageReviewer
from src.procedural.intent_planner import IntentPlanner
from src.procedural.intent_provider import IntentProvider
from src.procedural.plan_builder import PlanBuilder
from src.procedural.rule_based_provider import RuleBasedIntentProvider
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import RobotState, WorldState


@dataclass
class AgentResponse:
    """Comprehensive structured outcome returned by GroundedAgent."""

    question: str
    answer: str
    status: str
    perspectives: dict[str, str] | None = None
    revisions: list[RevisionOutcome] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)
    active_facts: list[StoredFact] = field(default_factory=list)
    sensor_telemetry: list[dict[str, Any]] = field(default_factory=list)
    graph: nx.MultiDiGraph = field(default_factory=nx.MultiDiGraph)
    plan_verifiable: bool = True


class GroundedAgent:
    """Three-tier cognitive agent that maintains grounded beliefs and provenance."""

    def __init__(
        self,
        composition: CompositionService,
        memory: MemoryRepository,
        graph: ActiveBeliefGraph,
        environment: MockEnvironment,
        evaluator: EpistemicEvaluator | None = None,
    ) -> None:
        self._composition = composition
        self._memory = memory
        self._graph = graph
        self._environment = environment
        self._evaluator = evaluator or EpistemicEvaluator()

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

    def ask(self, question: str) -> AgentResponse:
        """Execute one complete grounded query cycle: plan -> execute -> evaluate -> synthesize."""
        # 1. Execute plan via composition boundary
        plan_result = self._composition.process_question(question)

        # 2. Epistemically evaluate against memory and observations
        eval_outcome: EpistemicEvaluation = self._evaluator.evaluate(
            plan_result=plan_result,
            user_question=question,
            memory_repo=self._memory,
        )

        # 3. Pull current active projection and facts
        current_graph = self._graph.get_graph()
        from src.contracts import FactQuery

        active_facts = self._memory.query_facts(FactQuery(active_only=True))

        plan_verifiable = getattr(plan_result, "plan_verifiable", True)

        return AgentResponse(
            question=question,
            answer=eval_outcome.explanation,
            status=eval_outcome.status,
            perspectives=eval_outcome.perspectives,
            revisions=eval_outcome.revisions,
            audit_events=eval_outcome.audit_events,
            active_facts=active_facts,
            sensor_telemetry=eval_outcome.sensor_telemetry,
            graph=current_graph,
            plan_verifiable=plan_verifiable,
        )
