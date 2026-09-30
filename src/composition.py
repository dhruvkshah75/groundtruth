"""Application boundary wiring for Tier 2 planning and plan execution."""

from src.procedural.execution_results import PlanExecutionResult
from src.procedural.fallback_results import PlanningOutcome
from src.procedural.intent_planner import IntentPlanner, IntentPlannerOutcome
from src.procedural.plan_builder import PlanBuilder
from src.procedural.plan_executor import (
    EnvironmentInterface,
    MemoryRepositoryInterface,
    PlanExecutor,
)


class CompositionService:
    """Wires together intent planning and plan execution."""

    def __init__(
        self,
        planner: IntentPlanner,
        builder: PlanBuilder,
        memory: MemoryRepositoryInterface,
        environment: EnvironmentInterface,
    ):
        self._planner = planner
        self._builder = builder
        self._executor = PlanExecutor(memory_repository=memory, environment=environment)

    def process_question(
        self, user_question: str
    ) -> PlanExecutionResult | PlanningOutcome | IntentPlannerOutcome:
        """Plan and execute a user question.

        Returns:
            The executed results if planning succeeded, or the fallback outcome.
        """
        intent_outcome = self._planner.plan_intent(user_question)
        if intent_outcome.fallback is not None:
            return intent_outcome

        assert intent_outcome.intent is not None
        plan_outcome = self._builder.build(intent_outcome.intent)
        if plan_outcome.fallback is not None:
            return plan_outcome

        assert plan_outcome.plan is not None
        return self._executor.execute(plan_outcome.plan)
