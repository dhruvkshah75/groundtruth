"""Bounded review for supported wording classified as unsupported."""

from .capability_validator import CapabilityValidator


class IntentCoverageReviewer:
    """Find narrow supported intent candidates without creating a plan.

    Args:
        capability_validator: The fixed Tier 3 capability registry.
        history_available: Whether Tier 1 history retrieval is connected.
        audit_available: Whether Tier 1 audit retrieval is connected.
    """

    def __init__(
        self,
        capability_validator: CapabilityValidator,
        *,
        history_available: bool = False,
        audit_available: bool = False,
    ) -> None:
        """Store the available evidence interfaces for candidate checks.

        Args:
            capability_validator: The registry used for sensor gates.
            history_available: Enables historical-lookup candidates.
            audit_available: Enables audit-explanation candidates.
        """
        self._capability_validator = capability_validator
        self._history_available = history_available
        self._audit_available = audit_available

    def candidate_intents(self, user_question: str) -> tuple[str, ...]:
        """Return supported intent candidates for a normalized question.

        Args:
            user_question: The original user question to review.

        Returns:
            Zero, one, or several supported intent names. This method never
            resolves an entity, creates an operation, or calls a tool.
        """
        question = " ".join(user_question.lower().split())
        candidates: list[str] = []

        if self._matches_route_wording(question) and self._capability_validator.has_capability(
            "lidar_scan", "sensor"
        ):
            candidates.append("current_route_status")
        if self._matches_object_wording(question) and self._capability_validator.has_capability(
            "camera_detect", "sensor"
        ):
            candidates.append("current_object_perception")
        if self._matches_pose_wording(question) and self._capability_validator.has_capability(
            "robot_pose", "sensor"
        ):
            candidates.append("current_robot_pose")
        if self._matches_history_wording(question) and self._history_available:
            candidates.append("historical_fact_lookup")
        if self._matches_audit_wording(question) and self._audit_available:
            candidates.append("audit_explanation")

        return tuple(candidates)

    @staticmethod
    def _matches_route_wording(question: str) -> bool:
        """Check for a narrow route-status phrase family."""
        return any(
            word in question.split()
            for word in ("route", "path", "ahead", "forward", "blocked", "blocking", "obstacle")
        )

    @staticmethod
    def _matches_object_wording(question: str) -> bool:
        """Check for a narrow current-object-perception phrase family."""
        return any(word in question.split() for word in ("colour", "color", "appears")) or any(
            phrase in question for phrase in ("looks like", "visible object")
        )

    @staticmethod
    def _matches_pose_wording(question: str) -> bool:
        """Check for a narrow current-robot-pose phrase family."""
        return any(
            phrase in question
            for phrase in ("where are you", "current location", "current position")
        )

    @staticmethod
    def _matches_history_wording(question: str) -> bool:
        """Check for a narrow historical-lookup phrase family."""
        return any(
            word in question.split() for word in ("earlier", "before", "historical", "history")
        )

    @staticmethod
    def _matches_audit_wording(question: str) -> bool:
        """Check for a narrow audit-explanation phrase family."""
        return any(
            phrase in question
            for phrase in ("why changed", "why believe", "audit", "evidence history")
        )
