"""Deterministic mapping from validated intents to typed operations."""

from src.contracts import (
    EntityResolution,
    FactQuery,
    GroundedResult,
    IntentRequest,
    ObservationRequest,
)

from .capability_validator import CapabilityValidator
from .entity_resolver import EntityResolver
from .execution_plan import (
    AuditChainOperation,
    ExecutionPlan,
    MemoryQueryOperation,
    ObservationOperation,
)
from .fallback_results import FallbackCategory, PlanningFallback, PlanningOutcome


class PlanBuilder:
    """Build the fixed evidence plan required for each valid intent.

    Args:
        entity_resolver: Resolves user-facing names to canonical IDs.
        capability_validator: Checks the advertised Tier 3 capabilities.
    """

    def __init__(
        self,
        entity_resolver: EntityResolver,
        capability_validator: CapabilityValidator,
    ) -> None:
        """Store the dependencies used while creating plans.

        Args:
            entity_resolver: The injected entity-resolution interface.
            capability_validator: The fixed advertised-capability registry.
        """
        self._entity_resolver = entity_resolver
        self._capability_validator = capability_validator

    def build(self, intent: IntentRequest) -> PlanningOutcome:
        """Create the required typed operations for one validated intent.

        Args:
            intent: The validated intent received from ``IntentPlanner``.

        Returns:
            An approved plan or a safe fallback with no operations.
        """
        if intent.intent == "current_route_status":
            return self._build_route_status(intent)
        if intent.intent == "current_object_perception":
            return self._build_object_perception(intent)
        if intent.intent == "historical_fact_lookup":
            return self._build_historical_lookup(intent)
        if intent.intent == "audit_explanation":
            return self._build_audit_explanation(intent)
        if intent.intent == "current_robot_pose":
            return self._build_robot_pose(intent)
        if intent.intent == "environment_action":
            return self._fallback(
                "unsupported_intent",
                "Environment actions need a safe action name before they can be planned.",
            )
        return self._fallback(
            "unsupported_intent",
            "The request is outside the supported intent set.",
        )

    def _build_route_status(self, intent: IntentRequest) -> PlanningOutcome:
        """Build memory and LiDAR operations for a current route question."""
        entity = self._resolve_one_entity(intent)
        if isinstance(entity, PlanningFallback):
            return PlanningOutcome(fallback=entity)

        has_lidar = self._capability_validator.has_capability("lidar_scan", "sensor")
        observations = []
        blocking_reasons = []
        if has_lidar:
            observations.append(
                ObservationOperation(
                    request=ObservationRequest(capability="lidar_scan", target=entity),
                    purpose="Check the route with a fresh LiDAR scan.",
                )
            )
        else:
            blocking_reasons.append(
                "LiDAR is unavailable, so current route status is unverifiable."
            )

        return self._plan(
            intent=intent,
            entity_ids=[entity],
            memory_operations=[
                MemoryQueryOperation(
                    query=FactQuery(subject=entity, predicate="status_is"),
                    purpose="Read the active route status for context.",
                )
            ],
            observation_operations=observations,
            current_state_verifiable=has_lidar,
            blocking_reasons=blocking_reasons,
            reason="A current route check needs active memory and fresh LiDAR when available.",
        )

    def _build_object_perception(self, intent: IntentRequest) -> PlanningOutcome:
        """Build a camera operation for a current object-perception question."""
        entity = self._resolve_one_entity(intent)
        if isinstance(entity, PlanningFallback):
            return PlanningOutcome(fallback=entity)

        has_camera = self._capability_validator.has_capability("camera_detect", "sensor")
        observations = []
        blocking_reasons = []
        if has_camera:
            observations.append(
                ObservationOperation(
                    request=ObservationRequest(capability="camera_detect", target=entity),
                    purpose="Observe the object's current appearance.",
                )
            )
        else:
            blocking_reasons.append("Camera is unavailable, so current perception is unverifiable.")

        return self._plan(
            intent=intent,
            entity_ids=[entity],
            observation_operations=observations,
            current_state_verifiable=has_camera,
            blocking_reasons=blocking_reasons,
            reason="Current object perception requires a fresh camera observation.",
        )

    def _build_historical_lookup(self, intent: IntentRequest) -> PlanningOutcome:
        """Build a historical memory query without requesting a sensor."""
        entity = self._resolve_one_entity(intent)
        if isinstance(entity, PlanningFallback):
            return PlanningOutcome(fallback=entity)

        return self._plan(
            intent=intent,
            entity_ids=[entity],
            memory_operations=[
                MemoryQueryOperation(
                    query=FactQuery(subject=entity, active_only=False),
                    purpose="Read historical facts for the requested entity.",
                )
            ],
            current_state_verifiable=True,
            reason="Historical lookup needs stored facts and no live sensor.",
        )

    def _build_audit_explanation(self, intent: IntentRequest) -> PlanningOutcome:
        """Build an audit-history operation without creating explanation prose."""
        entity = self._resolve_one_entity(intent)
        if isinstance(entity, PlanningFallback):
            return PlanningOutcome(fallback=entity)

        return self._plan(
            intent=intent,
            entity_ids=[entity],
            audit_operations=[
                AuditChainOperation(
                    fact_or_entity_id=entity,
                    purpose="Read the evidence and revision history for this entity.",
                )
            ],
            current_state_verifiable=True,
            reason="Audit explanation needs an audit-history lookup.",
        )

    def _build_robot_pose(self, intent: IntentRequest) -> PlanningOutcome:
        """Build a robot-pose observation without using memory as a substitute."""
        has_pose = self._capability_validator.has_capability("robot_pose", "sensor")
        observations = []
        blocking_reasons = []
        if has_pose:
            observations.append(
                ObservationOperation(
                    request=ObservationRequest(capability="robot_pose"),
                    purpose="Read the robot's current pose.",
                )
            )
        else:
            blocking_reasons.append("Robot pose is unavailable, so current pose is unverifiable.")

        return self._plan(
            intent=intent,
            observation_operations=observations,
            current_state_verifiable=has_pose,
            blocking_reasons=blocking_reasons,
            reason="Current robot pose requires a fresh robot-pose observation.",
        )

    def _resolve_one_entity(self, intent: IntentRequest) -> str | PlanningFallback:
        """Resolve exactly one entity mention for an entity-based intent."""
        if not intent.entity_mentions:
            return self._new_fallback(
                "missing_entity",
                "This request needs one entity mention.",
            )
        if len(intent.entity_mentions) > 1:
            return self._new_fallback(
                "unsupported_intent",
                "This request supports one entity mention at a time.",
            )

        resolution = self._entity_resolver.resolve_entity(intent.entity_mentions[0])
        return self._entity_result(resolution)

    @staticmethod
    def _entity_result(resolution: EntityResolution) -> str | PlanningFallback:
        """Convert one resolver result into a canonical ID or safe fallback."""
        if resolution.status == "resolved":
            return resolution.canonical_entity_id
        if resolution.status == "ambiguous":
            return PlanBuilder._new_fallback(
                "ambiguous_entity",
                "The entity mention matches more than one known entity.",
                candidates=resolution.candidates,
            )
        return PlanBuilder._new_fallback(
            "missing_entity",
            "The entity mention does not match a known entity.",
        )

    @staticmethod
    def _plan(
        *,
        intent: IntentRequest,
        entity_ids: list[str] | None = None,
        memory_operations: list[MemoryQueryOperation] | None = None,
        observation_operations: list[ObservationOperation] | None = None,
        audit_operations: list[AuditChainOperation] | None = None,
        current_state_verifiable: bool,
        blocking_reasons: list[str] | None = None,
        reason: str,
    ) -> PlanningOutcome:
        """Wrap typed operations in one approved plan."""
        plan = ExecutionPlan(
            intent=intent,
            resolved_entity_ids=entity_ids or [],
            memory_operations=memory_operations or [],
            observation_operations=observation_operations or [],
            audit_operations=audit_operations or [],
            current_state_verifiable=current_state_verifiable,
            blocking_reasons=blocking_reasons or [],
            plan_reason=reason,
        )
        return PlanningOutcome(plan=plan)

    @staticmethod
    def _fallback(category: FallbackCategory, reason: str) -> PlanningOutcome:
        """Create a no-plan outcome for an unsupported request."""
        return PlanningOutcome(fallback=PlanBuilder._new_fallback(category, reason))

    @staticmethod
    def _new_fallback(
        category: FallbackCategory,
        reason: str,
        candidates: list[str] | None = None,
    ) -> PlanningFallback:
        """Create one uncertain fallback without evidence or operations."""
        return PlanningFallback(
            category=category,
            reason=reason,
            clarification_candidates=candidates or [],
            safe_result=GroundedResult(uncertainty=True, uncertainty_reason=reason),
        )
