"""Validation lifecycle for one provider-proposed intent."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.contracts import GroundedResult, IntentRequest

from .fallback_results import FallbackCategory, PlanningFallback
from .intent_coverage_reviewer import IntentCoverageReviewer
from .intent_provider import IntentProvider, IntentProviderUnavailableError


class IntentPlannerOutcome(BaseModel):
    """The validated intent or the safe reason intent planning stopped.

    Fields:
        intent: The validated intent that later stages may use.
        fallback: The safe failure result when no valid intent was obtained.
        matched_rule_ids: Coverage-rule IDs retained after unsupported review.
    """

    model_config = ConfigDict(extra="forbid")

    intent: IntentRequest | None = None
    fallback: PlanningFallback | None = None
    matched_rule_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def contain_exactly_one_result(self) -> "IntentPlannerOutcome":
        """Check that this outcome contains an intent or a fallback.

        Returns:
            The same validated outcome.
        """
        if (self.intent is None) == (self.fallback is None):
            raise ValueError("intent planner outcome requires exactly one result")
        return self


class IntentPlanner:
    """Turn one raw provider response into a valid intent or safe fallback.

    Args:
        provider: The narrow provider interface used for intent proposals.
    """

    def __init__(
        self,
        provider: IntentProvider,
        coverage_reviewer: IntentCoverageReviewer,
    ) -> None:
        """Store the provider used for intent proposals.

        Args:
            provider: The provider that proposes and repairs raw intent data.
            coverage_reviewer: The bounded reviewer for valid ``unsupported``
                responses.
        """
        self._provider = provider
        self._coverage_reviewer = coverage_reviewer

    def plan_intent(self, user_question: str) -> IntentPlannerOutcome:
        """Validate one intent proposal and at most one repair.

        Args:
            user_question: The original non-empty question from the user.

        Returns:
            An outcome containing a validated intent or a safe fallback.

        Raises:
            ValueError: If the user question is empty after trimming whitespace.
        """
        normalized_question = user_question.strip()
        if not normalized_question:
            raise ValueError("user_question must not be empty")

        try:
            raw_proposal = self._provider.propose_intent(normalized_question)
        except IntentProviderUnavailableError:
            return self._fallback(
                "provider_unavailable",
                "The intent provider did not return an initial proposal.",
            )

        intent, validation_error = self._validate_intent(raw_proposal, normalized_question)
        if intent is not None:
            return self._review_unsupported_if_needed(intent, normalized_question)

        try:
            raw_repair = self._provider.repair_intent(normalized_question, validation_error)
        except IntentProviderUnavailableError:
            return self._fallback(
                "provider_unavailable",
                "The intent provider did not return a repaired proposal.",
            )

        repaired_intent, _ = self._validate_intent(raw_repair, normalized_question)
        if repaired_intent is not None:
            return self._review_unsupported_if_needed(repaired_intent, normalized_question)

        return self._fallback(
            "invalid_provider_output_after_repair",
            "The intent provider returned invalid data after one repair attempt.",
        )

    def _review_unsupported_if_needed(
        self, intent: IntentRequest, normalized_question: str
    ) -> IntentPlannerOutcome:
        """Review one valid unsupported intent without directly creating a plan.

        Args:
            intent: The validated initial or repaired provider intent.
            normalized_question: The trimmed original user question.

        Returns:
            The valid intent, a reconsidered valid intent, or a safe fallback.
        """
        if intent.intent != "unsupported":
            return IntentPlannerOutcome(intent=intent)

        review = self._coverage_reviewer.review(normalized_question)
        if not review.candidate_intents:
            return self._fallback(
                "unsupported_after_review",
                "No supported intent matches this unsupported request.",
            )
        if len(review.candidate_intents) > 1:
            return self._fallback(
                "unsupported_after_review",
                "The request matches several supported intent categories and needs clarification.",
                intent_choices=list(review.candidate_intents),
                rule_ids=list(review.matched_rule_ids),
            )

        allowed_intents = (review.candidate_intents[0], "unsupported")
        try:
            raw_reconsidered = self._provider.reconsider_unsupported(
                normalized_question, allowed_intents
            )
        except IntentProviderUnavailableError:
            return self._fallback(
                "provider_unavailable",
                "The intent provider did not return an unsupported-intent reconsideration.",
                rule_ids=list(review.matched_rule_ids),
            )

        reconsidered, _ = self._validate_intent(raw_reconsidered, normalized_question)
        if reconsidered is None or reconsidered.intent not in allowed_intents:
            return self._fallback(
                "invalid_provider_output_after_unsupported_review",
                "The provider returned an invalid unsupported-intent reconsideration.",
                rule_ids=list(review.matched_rule_ids),
            )
        if reconsidered.intent == "unsupported":
            return self._fallback(
                "unsupported_after_review",
                "The provider kept the request unsupported after reconsideration.",
                rule_ids=list(review.matched_rule_ids),
            )
        return IntentPlannerOutcome(
            intent=reconsidered,
            matched_rule_ids=list(review.matched_rule_ids),
        )

    @staticmethod
    def _validate_intent(
        raw_intent: object, normalized_question: str
    ) -> tuple[IntentRequest | None, str]:
        """Validate raw data and preserve the original question.

        Args:
            raw_intent: Untrusted JSON-like data from the provider.
            normalized_question: The trimmed original user question.

        Returns:
            A valid intent and empty error text, or no intent and a short error.
        """
        try:
            intent = IntentRequest.model_validate(raw_intent)
        except ValidationError as error:
            return None, IntentPlanner._bounded_validation_error(error)

        if intent.user_question.strip() != normalized_question:
            return None, "user_question must match the original user question"

        return intent.model_copy(update={"user_question": normalized_question}), ""

    @staticmethod
    def _bounded_validation_error(error: ValidationError) -> str:
        """Create a short repair message without exposing raw provider data.

        Args:
            error: The Pydantic validation error for the raw provider response.

        Returns:
            A short field-and-message summary for one repair request.
        """
        first_error = error.errors()[0]
        location = ".".join(str(part) for part in first_error["loc"])
        message = str(first_error["msg"])
        return f"{location}: {message}"[:240]

    @staticmethod
    def _fallback(
        category: FallbackCategory,
        reason: str,
        *,
        intent_choices: list[str] | None = None,
        rule_ids: list[str] | None = None,
    ) -> IntentPlannerOutcome:
        """Create a safe no-intent result.

        Args:
            category: The stable fallback category for this failure.
            reason: A short safe reason for logs and later rendering.
            intent_choices: Intent categories available for clarification.
            rule_ids: Coverage-rule IDs retained for later diagnostics.

        Returns:
            An outcome with a fallback and no validated intent.
        """
        fallback = PlanningFallback(
            category=category,
            reason=reason,
            intent_clarification_choices=intent_choices or [],
            matched_rule_ids=rule_ids or [],
            safe_result=GroundedResult(uncertainty=True, uncertainty_reason=reason),
        )
        return IntentPlannerOutcome(fallback=fallback, matched_rule_ids=rule_ids or [])
