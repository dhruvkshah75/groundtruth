"""Bounded review for supported wording classified as unsupported."""

import re
from dataclasses import dataclass

from .capability_validator import CapabilityValidator


@dataclass(frozen=True)
class IntentCoverageReview:
    """The candidates and rule IDs found during one wording review.

    Fields:
        candidate_intents: Supported intent names found by the review.
        matched_rule_ids: Stable IDs for the rules that matched.
    """

    candidate_intents: tuple[str, ...]
    matched_rule_ids: tuple[str, ...]


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

    def review(self, user_question: str) -> IntentCoverageReview:
        """Review a normalized question against narrow documented rules.

        Args:
            user_question: The original user question to review.

        Returns:
            Candidate intent names and matching rule IDs. This method never
            resolves an entity, creates an operation, or calls a tool.
        """
        question = " ".join(user_question.lower().split())
        candidates: list[str] = []
        rule_ids: list[str] = []

        if self._matches_route_wording(question) and self._capability_validator.has_capability(
            "lidar_scan", "sensor"
        ):
            candidates.append("current_route_status")
            rule_ids.append("route_status_v1")
        if self._matches_object_wording(question) and self._capability_validator.has_capability(
            "camera_detect", "sensor"
        ):
            candidates.append("current_object_perception")
            rule_ids.append("object_perception_v1")
        if self._matches_pose_wording(question) and self._capability_validator.has_capability(
            "robot_pose", "sensor"
        ):
            candidates.append("current_robot_pose")
            rule_ids.append("robot_pose_v1")
        if self._matches_history_wording(question) and self._history_available:
            candidates.append("historical_fact_lookup")
            rule_ids.append("historical_lookup_v1")
        if self._matches_audit_wording(question) and self._audit_available:
            candidates.append("audit_explanation")
            rule_ids.append("audit_explanation_v1")

        return IntentCoverageReview(tuple(candidates), tuple(rule_ids))

    def candidate_intents(self, user_question: str) -> tuple[str, ...]:
        """Return only the candidates from one coverage review.

        Args:
            user_question: The original user question to review.

        Returns:
            Zero, one, or several supported intent names.
        """
        return self.review(user_question).candidate_intents

    @staticmethod
    def _matches_route_wording(question: str) -> bool:
        """Check route wording with word boundaries.

        Args:
            question: The normalized question to check.

        Returns:
            ``True`` when a documented route word is present.
        """
        return IntentCoverageReviewer._has_word(
            question,
            "route",
            "path",
            "ahead",
            "forward",
            "blocked",
            "blocking",
            "obstacle",
        )

    @staticmethod
    def _matches_object_wording(question: str) -> bool:
        """Check object-perception wording with word boundaries.

        Args:
            question: The normalized question to check.

        Returns:
            ``True`` when a documented object phrase is present.
        """
        return IntentCoverageReviewer._has_word(question, "colour", "color", "appears") or (
            IntentCoverageReviewer._has_phrase(question, "looks like", "visible object")
        )

    @staticmethod
    def _matches_pose_wording(question: str) -> bool:
        """Check robot-pose wording with phrase boundaries.

        Args:
            question: The normalized question to check.

        Returns:
            ``True`` when a documented pose phrase is present.
        """
        return IntentCoverageReviewer._has_phrase(
            question, "where are you", "current location", "current position"
        )

    @staticmethod
    def _matches_history_wording(question: str) -> bool:
        """Check historical wording with word boundaries.

        Args:
            question: The normalized question to check.

        Returns:
            ``True`` when a documented history word is present.
        """
        return IntentCoverageReviewer._has_word(
            question, "earlier", "before", "historical", "history"
        )

    @staticmethod
    def _matches_audit_wording(question: str) -> bool:
        """Check audit wording with word and phrase boundaries.

        Args:
            question: The normalized question to check.

        Returns:
            ``True`` when a documented audit phrase is present.
        """
        return IntentCoverageReviewer._has_word(question, "audit") or (
            IntentCoverageReviewer._has_phrase(
                question, "why changed", "why believe", "evidence history"
            )
        )

    @staticmethod
    def _has_word(question: str, *words: str) -> bool:
        """Check whether a complete documented word appears in the question.

        Args:
            question: The normalized question to search.
            *words: The documented words to match.

        Returns:
            ``True`` when any complete word matches.
        """
        return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", question) for word in words)

    @staticmethod
    def _has_phrase(question: str, *phrases: str) -> bool:
        """Check whether a complete documented phrase appears in the question.

        Args:
            question: The normalized question to search.
            *phrases: The documented phrases to match.

        Returns:
            ``True`` when any complete phrase matches.
        """
        return any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", question) for phrase in phrases)
