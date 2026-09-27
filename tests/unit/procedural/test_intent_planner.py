"""Tests for the bounded intent-validation lifecycle."""

import pytest

from src.contracts import CapabilityDescriptor, EntityResolution
from src.procedural import (
    CapabilityValidator,
    IntentCoverageReviewer,
    IntentPlanner,
    IntentProviderUnavailableError,
    PlanBuilder,
)


class QueuedIntentProvider:
    """Test provider with queued results and recorded calls.

    Fields:
        proposals: Results or exceptions for initial proposals.
        repairs: Results or exceptions for repair requests.
        calls: Provider calls received during a test.
    """

    def __init__(
        self,
        proposals: list[object],
        repairs: list[object] | None = None,
        reconsiderations: list[object] | None = None,
    ) -> None:
        """Set up queued provider responses.

        Args:
            proposals: Initial results or exceptions returned in order.
            repairs: Repair results or exceptions returned in order.
            reconsiderations: Unsupported-review results or exceptions in order.
        """
        self.proposals = proposals
        self.repairs = repairs or []
        self.reconsiderations = reconsiderations or []
        self.calls: list[tuple[object, ...]] = []

    def propose_intent(self, user_question: str) -> object:
        """Return the next initial proposal.

        Args:
            user_question: The question received from the planner.

        Returns:
            The next queued proposal or raises its queued exception.
        """
        self.calls.append(("propose", user_question))
        return self._next(self.proposals)

    def repair_intent(self, user_question: str, validation_error: str) -> object:
        """Return the next repair proposal.

        Args:
            user_question: The question received from the planner.
            validation_error: The bounded error summary from validation.

        Returns:
            The next queued repair or raises its queued exception.
        """
        self.calls.append(("repair", user_question, validation_error))
        return self._next(self.repairs)

    def reconsider_unsupported(
        self, user_question: str, candidate_intents: tuple[str, ...]
    ) -> object:
        """Return the next bounded unsupported-review result.

        Args:
            user_question: The question that would be reconsidered.
            candidate_intents: The restricted reconsideration choices.

        Returns:
            The next queued reconsideration or raises its queued exception.
        """
        self.calls.append(("reconsider", user_question, candidate_intents))
        return self._next(self.reconsiderations)

    @staticmethod
    def _next(values: list[object]) -> object:
        """Return the next queued value or raise it when it is an exception.

        Args:
            values: The remaining queued values.

        Returns:
            The next non-exception queued value.
        """
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def valid_route_intent(question: str) -> dict[str, object]:
    """Create a valid route-status payload for a test.

    Args:
        question: The user question to place in the payload.

    Returns:
        A JSON-like valid IntentRequest payload.
    """
    return {
        "intent": "current_route_status",
        "entity_mentions": ["front route"],
        "user_question": question,
    }


def reviewer_without_candidates() -> IntentCoverageReviewer:
    """Create the required reviewer with no available capabilities.

    Returns:
        A reviewer that keeps unrelated unsupported requests unsupported.
    """
    return IntentCoverageReviewer(CapabilityValidator([]))


def test_valid_first_response_returns_intent_without_repair() -> None:
    provider = QueuedIntentProvider([valid_route_intent("Can I move forward?")])

    outcome = IntentPlanner(provider, reviewer_without_candidates()).plan_intent(
        "Can I move forward?"
    )

    assert outcome.intent.intent == "current_route_status"
    assert outcome.fallback is None
    assert provider.calls == [("propose", "Can I move forward?")]


def test_invalid_first_response_uses_one_valid_repair() -> None:
    provider = QueuedIntentProvider(
        [{"intent": "route_check"}], [valid_route_intent("Can I move forward?")]
    )

    outcome = IntentPlanner(provider, reviewer_without_candidates()).plan_intent(
        "Can I move forward?"
    )

    assert outcome.intent.intent == "current_route_status"
    assert [call[0] for call in provider.calls] == ["propose", "repair"]


def test_invalid_response_after_repair_returns_safe_fallback() -> None:
    provider = QueuedIntentProvider([{"intent": "route_check"}], [{"intent": "still_bad"}])

    outcome = IntentPlanner(provider, reviewer_without_candidates()).plan_intent(
        "Can I move forward?"
    )

    assert outcome.intent is None
    assert outcome.fallback.category == "invalid_provider_output_after_repair"
    assert [call[0] for call in provider.calls] == ["propose", "repair"]


def test_provider_exception_returns_safe_fallback() -> None:
    provider = QueuedIntentProvider([IntentProviderUnavailableError("provider offline")])

    outcome = IntentPlanner(provider, reviewer_without_candidates()).plan_intent(
        "Can I move forward?"
    )

    assert outcome.intent is None
    assert outcome.fallback.category == "provider_unavailable"
    assert provider.calls == [("propose", "Can I move forward?")]


def test_unexpected_provider_error_remains_visible_during_development() -> None:
    provider = QueuedIntentProvider([RuntimeError("programming error")])

    with pytest.raises(RuntimeError, match="programming error"):
        IntentPlanner(provider, reviewer_without_candidates()).plan_intent("Can I move forward?")


def test_mismatched_user_question_uses_repair_and_normalizes_whitespace() -> None:
    provider = QueuedIntentProvider(
        [valid_route_intent("Different question")],
        [valid_route_intent(" Can I move forward? ")],
    )

    outcome = IntentPlanner(provider, reviewer_without_candidates()).plan_intent(
        " Can I move forward? "
    )

    assert outcome.intent.user_question == "Can I move forward?"
    assert [call[0] for call in provider.calls] == ["propose", "repair"]


def test_explicit_unsupported_is_valid_without_repair() -> None:
    provider = QueuedIntentProvider(
        [
            {
                "intent": "unsupported",
                "entity_mentions": [],
                "user_question": "What is the room temperature?",
            }
        ]
    )

    outcome = IntentPlanner(provider, reviewer_without_candidates()).plan_intent(
        "What is the room temperature?"
    )

    assert outcome.intent is None
    assert outcome.fallback.category == "unsupported_after_review"
    assert provider.calls == [("propose", "What is the room temperature?")]


def test_empty_question_is_rejected_without_provider_call() -> None:
    provider = QueuedIntentProvider([valid_route_intent("unused")])

    with pytest.raises(ValueError, match="must not be empty"):
        IntentPlanner(provider, reviewer_without_candidates()).plan_intent("   ")

    assert provider.calls == []


def test_supported_candidate_gets_one_constrained_reconsideration() -> None:
    question = "Is anything blocking me?"
    provider = QueuedIntentProvider(
        [{"intent": "unsupported", "entity_mentions": [], "user_question": question}],
        reconsiderations=[valid_route_intent(question)],
    )
    reviewer = IntentCoverageReviewer(
        CapabilityValidator(
            [
                CapabilityDescriptor(
                    name="lidar_scan",
                    kind="sensor",
                    description="Checks obstacles in front of the robot.",
                )
            ]
        )
    )

    outcome = IntentPlanner(provider, reviewer).plan_intent(question)

    assert outcome.intent.intent == "current_route_status"
    assert provider.calls == [
        ("propose", question),
        ("reconsider", question, ("current_route_status", "unsupported")),
    ]


def test_recovered_intent_can_continue_to_entity_resolution_and_plan_building() -> None:
    question = "Is anything blocking me?"
    provider = QueuedIntentProvider(
        [{"intent": "unsupported", "entity_mentions": [], "user_question": question}],
        reconsiderations=[valid_route_intent(question)],
    )
    validator = CapabilityValidator(
        [
            CapabilityDescriptor(
                name="lidar_scan",
                kind="sensor",
                description="Checks obstacles in front of the robot.",
            )
        ]
    )
    reviewer = IntentCoverageReviewer(validator)

    class Resolver:
        def resolve_entity(self, mention: str) -> EntityResolution:
            return EntityResolution(
                mention=mention,
                status="resolved",
                canonical_entity_id="route_ahead",
            )

    outcome = IntentPlanner(provider, reviewer).plan_intent(question)
    plan_outcome = PlanBuilder(Resolver(), validator).build(outcome.intent)

    assert plan_outcome.plan.resolved_entity_ids == ["route_ahead"]
    assert plan_outcome.plan.observation_operations[0].request.capability == "lidar_scan"


def test_reconsidered_unsupported_remains_a_no_plan_fallback() -> None:
    question = "Is anything blocking me?"
    provider = QueuedIntentProvider(
        [{"intent": "unsupported", "entity_mentions": [], "user_question": question}],
        reconsiderations=[
            {"intent": "unsupported", "entity_mentions": [], "user_question": question}
        ],
    )
    reviewer = IntentCoverageReviewer(
        CapabilityValidator(
            [
                CapabilityDescriptor(
                    name="lidar_scan",
                    kind="sensor",
                    description="Checks obstacles in front of the robot.",
                )
            ]
        )
    )

    outcome = IntentPlanner(provider, reviewer).plan_intent(question)

    assert outcome.intent is None
    assert outcome.fallback.category == "unsupported_after_review"
    assert [call[0] for call in provider.calls] == ["propose", "reconsider"]


def test_unsupported_with_no_candidate_stays_a_no_plan_fallback() -> None:
    question = "What is the room temperature?"
    provider = QueuedIntentProvider(
        [{"intent": "unsupported", "entity_mentions": [], "user_question": question}]
    )
    reviewer = IntentCoverageReviewer(
        CapabilityValidator(
            [
                CapabilityDescriptor(
                    name="lidar_scan",
                    kind="sensor",
                    description="Checks obstacles in front of the robot.",
                )
            ]
        )
    )

    outcome = IntentPlanner(provider, reviewer).plan_intent(question)

    assert outcome.intent is None
    assert outcome.fallback.category == "unsupported_after_review"
    assert provider.calls == [("propose", question)]


def test_invalid_reconsideration_returns_safe_failure_without_retry() -> None:
    question = "Is anything blocking me?"
    provider = QueuedIntentProvider(
        [{"intent": "unsupported", "entity_mentions": [], "user_question": question}],
        reconsiderations=[{"intent": "read_weather", "user_question": question}],
    )
    reviewer = IntentCoverageReviewer(
        CapabilityValidator(
            [
                CapabilityDescriptor(
                    name="lidar_scan",
                    kind="sensor",
                    description="Checks obstacles in front of the robot.",
                )
            ]
        )
    )

    outcome = IntentPlanner(provider, reviewer).plan_intent(question)

    assert outcome.intent is None
    assert outcome.fallback.category == "invalid_provider_output_after_unsupported_review"
    assert [call[0] for call in provider.calls] == ["propose", "reconsider"]


def test_reconsideration_cannot_choose_an_out_of_set_intent() -> None:
    question = "Is anything blocking me?"
    provider = QueuedIntentProvider(
        [{"intent": "unsupported", "entity_mentions": [], "user_question": question}],
        reconsiderations=[
            {
                "intent": "current_robot_pose",
                "entity_mentions": [],
                "user_question": question,
            }
        ],
    )
    reviewer = IntentCoverageReviewer(
        CapabilityValidator(
            [
                CapabilityDescriptor(
                    name="lidar_scan",
                    kind="sensor",
                    description="Checks obstacles in front of the robot.",
                )
            ]
        )
    )

    outcome = IntentPlanner(provider, reviewer).plan_intent(question)

    assert outcome.intent is None
    assert outcome.fallback.category == "invalid_provider_output_after_unsupported_review"
    assert [call[0] for call in provider.calls] == ["propose", "reconsider"]


def test_several_candidates_return_fallback_without_reconsideration() -> None:
    question = "Is an obstacle ahead and what color is it?"
    provider = QueuedIntentProvider(
        [{"intent": "unsupported", "entity_mentions": [], "user_question": question}]
    )
    reviewer = IntentCoverageReviewer(
        CapabilityValidator(
            [
                CapabilityDescriptor(
                    name="lidar_scan",
                    kind="sensor",
                    description="Checks obstacles in front of the robot.",
                ),
                CapabilityDescriptor(
                    name="camera_detect",
                    kind="sensor",
                    description="Checks visible objects.",
                ),
            ]
        )
    )

    outcome = IntentPlanner(provider, reviewer).plan_intent(question)

    assert outcome.intent is None
    assert outcome.fallback.category == "unsupported_after_review"
    assert outcome.fallback.intent_clarification_choices == [
        "current_route_status",
        "current_object_perception",
    ]
    assert outcome.fallback.matched_rule_ids == ["route_status_v1", "object_perception_v1"]
    assert provider.calls == [("propose", question)]
