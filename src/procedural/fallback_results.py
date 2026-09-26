"""Typed outcomes for requests that cannot safely produce a plan."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.contracts import GroundedResult

from .execution_plan import ExecutionPlan

FallbackCategory = Literal[
    "invalid_provider_output_after_repair",
    "provider_unavailable",
    "unsupported_intent",
    "unsupported_after_review",
    "invalid_provider_output_after_unsupported_review",
    "unknown_capability",
    "required_capability_unavailable",
    "missing_entity",
    "ambiguous_entity",
    "invalid_configuration",
]
IntentChoice = Literal[
    "current_route_status",
    "current_object_perception",
    "historical_fact_lookup",
    "audit_explanation",
    "current_robot_pose",
    "environment_action",
    "unsupported",
]
CoverageRuleId = Literal[
    "route_status_v1",
    "object_perception_v1",
    "robot_pose_v1",
    "historical_lookup_v1",
    "audit_explanation_v1",
]


class PlanningFallback(BaseModel):
    """A safe reason why planning stopped without creating operations.

    Fields:
        category: Stable label for the reason planning stopped.
        reason: Short diagnostic explanation for logs and tests.
        safe_result: An uncertain result with no claim, policy, or evidence.
        clarification_candidates: Canonical IDs the user may clarify between.
        intent_clarification_choices: Intent names the user may clarify between.
        matched_rule_ids: Coverage-rule IDs retained for later diagnostics.
    """

    model_config = ConfigDict(extra="forbid")

    category: FallbackCategory
    reason: str = Field(min_length=1)
    safe_result: GroundedResult
    clarification_candidates: list[str] = Field(default_factory=list)
    intent_clarification_choices: list[IntentChoice] = Field(default_factory=list)
    matched_rule_ids: list[CoverageRuleId] = Field(default_factory=list)

    @model_validator(mode="after")
    def fallback_must_be_uncertain_and_evidence_free(self) -> "PlanningFallback":
        """Check this fallback has no factual claim or evidence.

        Returns:
            The same validated fallback.
        """
        if not self.safe_result.uncertainty:
            raise ValueError("planning fallback requires an uncertain safe_result")
        if self.safe_result.conclusion is not None:
            raise ValueError("planning fallback cannot contain a conclusion")
        if self.safe_result.evidence_ids:
            raise ValueError("planning fallback cannot contain evidence_ids")
        if self.safe_result.conflicting_ids:
            raise ValueError("planning fallback cannot contain conflicting_ids")
        if self.safe_result.policy_rule is not None:
            raise ValueError("planning fallback cannot contain a policy_rule")

        if self.category == "ambiguous_entity":
            if len(self.clarification_candidates) < 2:
                raise ValueError("ambiguous_entity requires at least two candidates")
            if any(not candidate.strip() for candidate in self.clarification_candidates):
                raise ValueError("clarification candidates cannot be empty")
            if any(candidate != candidate.strip() for candidate in self.clarification_candidates):
                raise ValueError("clarification candidates cannot have surrounding whitespace")
            if len(set(self.clarification_candidates)) != len(self.clarification_candidates):
                raise ValueError("clarification candidates must be unique")
        elif self.clarification_candidates:
            raise ValueError("clarification candidates are only valid for ambiguous_entity")

        if self.category == "unsupported_after_review":
            if self.intent_clarification_choices and (
                len(self.intent_clarification_choices) < 2
                or len(set(self.intent_clarification_choices))
                != len(self.intent_clarification_choices)
                or any(not choice.strip() for choice in self.intent_clarification_choices)
            ):
                raise ValueError("intent clarification choices must be unique and non-empty")
            if any(choice != choice.strip() for choice in self.intent_clarification_choices):
                raise ValueError("intent clarification choices cannot have surrounding whitespace")
        elif self.intent_clarification_choices:
            raise ValueError(
                "intent clarification choices are only valid for unsupported_after_review"
            )
        if len(set(self.matched_rule_ids)) != len(self.matched_rule_ids):
            raise ValueError("matched rule IDs must be unique")
        return self


class PlanningOutcome(BaseModel):
    """Either an executable plan or a safe no-plan result.

    Fields:
        plan: The approved plan, when planning can continue.
        fallback: The safe reason planning stopped, when it cannot continue.
    """

    model_config = ConfigDict(extra="forbid")

    plan: ExecutionPlan | None = None
    fallback: PlanningFallback | None = None

    @model_validator(mode="after")
    def contain_exactly_one_result(self) -> "PlanningOutcome":
        """Check that this outcome has one result only.

        Returns:
            The same validated outcome.
        """
        if (self.plan is None) == (self.fallback is None):
            raise ValueError("planning outcome requires exactly one of plan or fallback")
        return self
