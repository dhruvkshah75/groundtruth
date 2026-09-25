"""Pydantic data contracts shared by GroundTruth's three tiers."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


class SpatialContext(BaseModel):
    """Scope a fact or observation to a place, viewpoint, and world state.

    ``frame_of_reference`` makes directional claims such as "in front"
    unambiguous: the robot's front changes when it turns. ``world_version``
    identifies the changing mock-world state that produced the claim.

    Attributes:
        location: Canonical place, for example ``room_101``.
        frame_of_reference: Viewpoint, for example ``robot_base``.
        world_version: Non-negative mock-world state number.
        observer: Observing agent, for example ``robot_01``.
        extra_context: Small JSON-safe scope details, for example lighting.
    """

    model_config = ConfigDict(extra="forbid")

    location: str | None = None
    frame_of_reference: str | None = None
    world_version: int | None = Field(default=None, ge=0)
    observer: str | None = None
    extra_context: dict[str, JsonValue] = Field(default_factory=dict)


class FactAssertion(BaseModel):
    """A sourced claim that Tier 2 asks Tier 1 to store.

    The triple ``subject predicate object`` describes the claim. ``context``
    states where it applies, while ``evidence`` keeps small JSON-safe details
    that support it. This is not a stored database row yet; Tier 1 later adds
    its fact ID and storage time.

    Example: ``route_A status_is blocked`` means subject ``route_A``,
    predicate ``status_is``, and object ``blocked``.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"] = "v1"
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)
    source_agent: str = Field(min_length=1)
    confidence_score: float = Field(ge=0.0, le=1.0)
    observed_at: datetime
    context: SpatialContext = Field(default_factory=SpatialContext)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_include_timezone(cls, value: datetime) -> datetime:
        """Reject naive timestamps because their real-world time is unclear."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class StoredFact(FactAssertion):
    """A FactAssertion that Tier 1 has durably stored in SQLite.

    ``FactAssertion`` is the short-lived, validated request sent to Tier 1.
    Once Tier 1 accepts and saves it, it returns this model with a permanent
    ``fact_id`` and ``created_at`` time. If a later claim becomes the active
    belief, ``superseded_by`` points to that later fact's UUID; the old fact
    remains available as historical evidence.

    Attributes:
        fact_id: Unique UUID assigned by Tier 1 to this stored fact.
        created_at: Time at which Tier 1 persisted the fact.
        superseded_by: UUID of the replacing fact, or ``None`` if active.
    """

    fact_id: UUID
    created_at: datetime
    superseded_by: UUID | None = None

    @field_validator("created_at")
    @classmethod
    def created_at_must_include_timezone(cls, value: datetime) -> datetime:
        """Reject naive storage timestamps because audit order needs a timezone."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class FactQuery(BaseModel):
    """A safe, structured request for Tier 1 facts instead of raw SQL.

    Fields narrow the search. Leaving a field as ``None`` means it should not
    filter results. ``active_only`` selects current beliefs by default, while
    ``as_of`` asks what was known at a specific past time.

    Attributes:
        subject: Entity to search for, for example ``route_A``.
        predicate: Fact relationship to search for, for example ``status_is``.
        source_agent: Optional source filter, for example ``static_map``.
        active_only: Whether superseded facts are excluded.
        as_of: Optional timezone-aware time for a historical query.
        context: Optional location/viewpoint filter for the query.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, min_length=1)
    predicate: str | None = Field(default=None, min_length=1)
    source_agent: str | None = Field(default=None, min_length=1)
    active_only: bool = True
    as_of: datetime | None = None
    context: SpatialContext | None = None

    @field_validator("as_of")
    @classmethod
    def as_of_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous historical query times."""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value


class BeliefRevision(BaseModel):
    """Tell Tier 1 that a new stored fact replaces an active older belief.

    Tier 2 creates this only after it has queried the older ``StoredFact``,
    stored the new assertion, and applied deterministic evidence policy. Tier
    1 uses the two UUIDs to link the old fact's ``superseded_by`` field and
    save an audit event; Tier 2 never writes that database change directly.

    Attributes:
        old_fact_id: Active stored fact that is being replaced.
        new_fact_id: New stored fact that becomes the operational belief.
        reason: Human-readable evidence summary for the change.
        policy_rule: Stable name of the deterministic rule used.
        revised_at: Time at which Tier 2 made the revision decision.
    """

    model_config = ConfigDict(extra="forbid")

    old_fact_id: UUID
    new_fact_id: UUID
    reason: str = Field(min_length=1)
    policy_rule: str = Field(min_length=1)
    revised_at: datetime

    @field_validator("revised_at")
    @classmethod
    def revised_at_must_include_timezone(cls, value: datetime) -> datetime:
        """Reject naive revision times because audit ordering needs a timezone."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("revised_at must be timezone-aware")
        return value


class AuditEvent(BaseModel):
    """Tier 1's permanent proof that a memory event was completed.

    For a belief revision, input facts include the old and new claims, while
    output facts identify the claim selected as operationally active. Lists
    keep the format usable for future events involving more than two facts.

    Attributes:
        event_id: Unique UUID assigned by Tier 1 to this event.
        event_type: Kind of event, for example ``belief_revision``.
        input_fact_ids: Fact UUIDs considered by the event.
        output_fact_ids: Fact UUIDs selected or produced by the event.
        reason: Human-readable explanation for the event.
        policy_rule: Stable deterministic rule that justified the outcome.
        created_at: Time at which Tier 1 recorded the event.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: str = Field(min_length=1)
    input_fact_ids: list[UUID] = Field(min_length=1)
    output_fact_ids: list[UUID] = Field(min_length=1)
    reason: str = Field(min_length=1)
    policy_rule: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_must_include_timezone(cls, value: datetime) -> datetime:
        """Reject naive audit times because event order must be unambiguous."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class AuditTrail(BaseModel):
    """The facts and audit events Tier 1 returns to explain one belief.

    Tier 2 requests this after a user asks a question such as "why do you
    believe route_A is blocked?" The trail contains the requested root fact,
    its relevant historical facts, and any revision events that connect them.

    Attributes:
        root_fact_id: Fact UUID whose explanation was requested.
        facts: Stored facts needed to explain the belief history.
        events: Audit events that explain changes in that history.
    """

    model_config = ConfigDict(extra="forbid")

    root_fact_id: UUID
    facts: list[StoredFact] = Field(min_length=1)
    events: list[AuditEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def root_fact_must_be_present(self) -> "AuditTrail":
        """Ensure a trail actually contains the fact it claims to explain."""
        if self.root_fact_id not in {fact.fact_id for fact in self.facts}:
            raise ValueError("root_fact_id must be included in facts")
        return self


class EntityResolution(BaseModel):
    """Report whether a user-friendly entity name maps to a known ID.

    Tier 1 returns this after resolving names such as ``the box``. Tier 2 can
    continue with one resolved ID, ask a clarification for several candidates,
    or report no known entity for a missing match.
    """

    model_config = ConfigDict(extra="forbid")

    mention: str = Field(min_length=1)
    status: Literal["resolved", "ambiguous", "missing"]
    canonical_entity_id: str | None = None
    candidates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def status_must_match_resolution_data(self) -> "EntityResolution":
        """Keep resolved, ambiguous, and missing results unambiguous."""
        if self.status == "resolved" and self.canonical_entity_id is None:
            raise ValueError("resolved status requires canonical_entity_id")
        if self.status == "ambiguous" and (
            self.canonical_entity_id is not None or len(self.candidates) < 2
        ):
            raise ValueError("ambiguous status requires at least two candidates")
        if self.status == "missing" and (
            self.canonical_entity_id is not None or self.candidates
        ):
            raise ValueError("missing status cannot include entity candidates")
        return self


class CapabilityDescriptor(BaseModel):
    """Describe one sensor or action that the current Tier 3 environment supports."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["sensor", "action"]
    description: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)


class ObservationRequest(BaseModel):
    """Ask Tier 3 to perform one registered observation capability.

    ``target`` optionally identifies what to inspect, while ``parameters``
    contains small JSON-safe settings such as a LiDAR direction.
    """

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=1)
    target: str | None = Field(default=None, min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class SensorObservation(BaseModel):
    """A successful current observation returned by Tier 3.

    Measurements contain the sensor result, while context describes where and
    from whose viewpoint it was observed. Tier 2 later decides whether this
    observation supports storing a new fact.
    """

    model_config = ConfigDict(extra="forbid")

    observation_id: UUID
    sensor: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    observed_at: datetime
    confidence_score: float = Field(ge=0.0, le=1.0)
    measurements: dict[str, JsonValue] = Field(min_length=1)
    context: SpatialContext = Field(default_factory=SpatialContext)

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_include_timezone(cls, value: datetime) -> datetime:
        """Reject naive observation times because freshness needs a timezone."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class ObservationUnavailable(BaseModel):
    """Explain why Tier 3 could not produce a requested observation."""

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=1)
    reason: Literal[
        "unsupported_capability",
        "sensor_unavailable",
        "target_not_visible",
    ]
    message: str = Field(min_length=1)
    reported_at: datetime

    @field_validator("reported_at")
    @classmethod
    def reported_at_must_include_timezone(cls, value: datetime) -> datetime:
        """Reject naive failure times for the same reason as observations."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reported_at must be timezone-aware")
        return value


class IntentRequest(BaseModel):
    """The limited intent an LLM/provider may propose for a user question.

    Tier 2 validates this request, resolves its entity mentions, and builds
    the real memory/sensor plan deterministically.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "current_route_status",
        "current_object_perception",
        "historical_fact_lookup",
        "audit_explanation",
        "current_robot_pose",
        "environment_action",
        "unsupported",
    ]
    entity_mentions: list[str] = Field(default_factory=list)
    user_question: str = Field(min_length=1)


class GroundedResult(BaseModel):
    """Deterministic evidence result that Tier 2 gives to the response layer.

    A resolved conclusion must name real evidence and the rule used. An
    uncertain result must instead explain why the agent cannot verify a claim.
    """

    model_config = ConfigDict(extra="forbid")

    conclusion: str | None = None
    evidence_ids: list[UUID] = Field(default_factory=list)
    conflicting_ids: list[UUID] = Field(default_factory=list)
    policy_rule: str | None = None
    uncertainty: bool
    uncertainty_reason: str | None = None

    @model_validator(mode="after")
    def result_must_match_uncertainty_state(self) -> "GroundedResult":
        """Prevent confident environment claims without supporting evidence."""
        if self.uncertainty:
            if not self.uncertainty_reason:
                raise ValueError("uncertain result requires uncertainty_reason")
            return self

        if not self.conclusion or not self.evidence_ids or not self.policy_rule:
            raise ValueError(
                "resolved result requires conclusion, evidence_ids, and policy_rule"
            )
        return self
