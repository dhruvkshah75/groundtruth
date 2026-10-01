"""Epistemic evaluation and conflict resolution engine (Tier 2).

Evaluates execution results against long-term memory and physical observations to:
1. Verify semantic groundedness (Scenario A: Map vs. LiDAR conflict detection and belief revision).
2. Track multi-perspective epistemic states (Scenario B: User, Egocentric, Historical).
3. Handle unverifiable or ambiguous states safely without hallucination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from src.contracts import (
    AuditEvent,
    AuditTrail,
    FactAssertion,
    FactQuery,
    ObservationUnavailable,
    SensorObservation,
    StoredFact,
)
from src.procedural.execution_results import PlanExecutionResult
from src.procedural.fallback_results import PlanningOutcome
from src.procedural.intent_planner import IntentPlannerOutcome


@runtime_checkable
class EpistemicMemoryInterface(Protocol):
    """Protocol defining the memory operations needed by epistemic evaluation."""

    def query_facts(self, query: FactQuery) -> list[StoredFact]: ...

    def record_revision(
        self,
        old_fact_id: UUID,
        replacement: FactAssertion,
        reason: str,
        policy_rule: str,
        revised_at: datetime,
    ) -> Any: ...


@dataclass
class EpistemicEvaluation:
    """Outcome of epistemic evaluation across memory and sensor layers."""

    status: str
    explanation: str
    revisions: list[Any] = field(default_factory=list)
    perspectives: dict[str, str] | None = None
    sensor_telemetry: list[dict[str, Any]] = field(default_factory=list)
    memory_facts: list[StoredFact] = field(default_factory=list)
    audit_trails: list[AuditTrail] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)


class EpistemicEvaluator:
    """Evaluates plan execution results and resolves epistemic contradictions."""

    def __init__(self, clock: Any = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        plan_result: PlanExecutionResult | PlanningOutcome | IntentPlannerOutcome,
        user_question: str,
        memory_repo: EpistemicMemoryInterface,
    ) -> EpistemicEvaluation:
        """Perform epistemic evaluation of execution results against memory and sensors."""
        # 1. Handle planning-level fallbacks
        if isinstance(plan_result, IntentPlannerOutcome):
            fallback = plan_result.fallback
            reason = fallback.reason if fallback else "Failed to parse intent."
            return EpistemicEvaluation(
                status="unsupported",
                explanation=f"Cannot process request: {reason}",
            )

        if isinstance(plan_result, PlanningOutcome):
            fallback = plan_result.fallback
            category = fallback.category if fallback else "unknown"
            reason = fallback.reason if fallback else "Planning failed."
            if category == "ambiguous_entity":
                candidates = fallback.clarification_candidates if fallback else []
                cand_str = ", ".join(candidates) if candidates else "multiple matches"
                return EpistemicEvaluation(
                    status="ambiguous",
                    explanation=(
                        f"Ambiguous entity mention in query. Candidates found: [{cand_str}]. "
                        "Please clarify which entity you mean."
                    ),
                )
            if category == "missing_entity":
                return EpistemicEvaluation(
                    status="unrecognized_entity",
                    explanation=f"Unrecognized entity: {reason}. No matching records in memory.",
                )
            return EpistemicEvaluation(
                status="unsupported",
                explanation=f"Request could not be planned: {reason}",
            )

        # 2. Check for unverifiable sensor state
        if not plan_result.plan_verifiable or any(
            isinstance(obs.result, ObservationUnavailable)
            for obs in plan_result.observation_results
        ):
            reasons = plan_result.blocking_reasons or ["Required sensor capability is unavailable."]
            unavailable = [
                obs.result.message
                for obs in plan_result.observation_results
                if isinstance(obs.result, ObservationUnavailable)
            ]
            reasons = list(dict.fromkeys([*reasons, *unavailable]))
            reason_str = "; ".join(reasons)
            # Collect memory facts if available
            facts = [f for mem in plan_result.memory_results for f in mem.facts]
            return EpistemicEvaluation(
                status="unverifiable",
                explanation=(
                    f"Current physical state cannot be verified because: {reason_str}. "
                    "Preserving stored memory without ungrounded assumptions."
                ),
                memory_facts=facts,
                sensor_telemetry=[
                    obs.result.model_dump(mode="json") for obs in plan_result.observation_results
                ],
            )

        # 3. Collect observations and memory facts
        observations: list[SensorObservation] = [
            obs.result
            for obs in plan_result.observation_results
            if isinstance(obs.result, SensorObservation)
        ]
        telemetry = [obs.model_dump(mode="json") for obs in observations]
        memory_facts = [f for mem in plan_result.memory_results for f in mem.facts]

        # 4. Scenario B: Perspective Tracking (Theory of Mind)
        # Check if the query is asking about perspectives or object appearance/color
        lower_q = user_question.lower()
        if (
            "perspective" in lower_q
            or ("color" in lower_q and "think" in lower_q)
            or ("user" in lower_q and "sensor" in lower_q)
            or any(obs.capability == "camera_detect" for obs in observations)
        ):
            return self._evaluate_perspectives(
                user_question=user_question,
                observations=observations,
                memory_repo=memory_repo,
            )

        # 5. Scenario A: Groundedness Verification (Map vs. LiDAR Reality)
        lidar_obs = next((obs for obs in observations if obs.capability == "lidar_scan"), None)
        if lidar_obs:
            return self._evaluate_route_groundedness(
                lidar_obs=lidar_obs,
                memory_facts=memory_facts,
                memory_repo=memory_repo,
            )

        # A successful pose observation is direct evidence for current-location
        # questions. Keep its position and frame context in the evaluated result.
        pose_obs = next((obs for obs in observations if obs.capability == "robot_pose"), None)
        if pose_obs:
            measurements = pose_obs.measurements
            required_measurements = ("robot_id", "x_cm", "y_cm", "direction")
            missing = [key for key in required_measurements if measurements.get(key) is None]
            if missing:
                return EpistemicEvaluation(
                    status="unverifiable",
                    explanation=(
                        "The pose sensor returned an incomplete reading; missing "
                        f"{', '.join(missing)}."
                    ),
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )

            context_parts = [
                value
                for value in (
                    pose_obs.context.location,
                    f"frame of reference: {pose_obs.context.frame_of_reference}"
                    if pose_obs.context.frame_of_reference
                    else None,
                )
                if value
            ]
            context_text = f" in {', '.join(context_parts)}" if context_parts else ""
            explanation = (
                f"The robot ({measurements['robot_id']}) is at x={measurements['x_cm']} cm, "
                f"y={measurements['y_cm']} cm{context_text}, facing "
                f"{measurements['direction']}."
            )
            return EpistemicEvaluation(
                status="verified",
                explanation=explanation,
                memory_facts=memory_facts,
                sensor_telemetry=telemetry,
            )

        # 6. Audit lookups
        if plan_result.audit_results:
            trails: list[AuditTrail] = []
            events: list[AuditEvent] = []
            for ar in plan_result.audit_results:
                for trail in ar.trails.values():
                    trails.append(trail)
                    events.extend(trail.events)
            explanation = (
                f"Audit trail retrieved with {len(events)} revision event(s) "
                "explaining past belief changes."
                if events
                else "No revision history found for the requested entity."
            )
            return EpistemicEvaluation(
                status="audit_retrieved",
                explanation=explanation,
                memory_facts=memory_facts,
                audit_trails=trails,
                audit_events=events,
            )

        # 7. General historical / fact lookup
        if memory_facts:
            facts_summary = "; ".join(
                f"{f.subject} {f.predicate} {f.object} (source: {f.source_agent})"
                for f in memory_facts
            )
            return EpistemicEvaluation(
                status="verified",
                explanation=f"Retrieved stored facts from memory: {facts_summary}.",
                memory_facts=memory_facts,
                sensor_telemetry=telemetry,
            )

        return EpistemicEvaluation(
            status="no_data",
            explanation="No facts found in memory and no active observations recorded.",
        )

    def _evaluate_route_groundedness(
        self,
        lidar_obs: SensorObservation,
        memory_facts: list[StoredFact],
        memory_repo: EpistemicMemoryInterface,
    ) -> EpistemicEvaluation:
        """Scenario A: compare active map belief with live LiDAR and revise if conflicted."""
        measurements = lidar_obs.measurements
        sensor_status = measurements.get("status")
        nearest_dist = measurements.get("nearest_distance_cm")
        telemetry = [lidar_obs.model_dump(mode="json")]

        # A route claim only applies to this observed place and frame of reference.
        map_facts = [
            f
            for f in memory_facts
            if f.predicate == "status_is"
            and f.superseded_by is None
            and f.object in ("clear", "blocked")
        ]
        contextual_facts = [
            fact for fact in map_facts if _contexts_match(fact.context, lidar_obs.context)
        ]
        if map_facts and not contextual_facts:
            return EpistemicEvaluation(
                status="unverifiable",
                explanation=(
                    "The live LiDAR observation and stored route claim use different spatial "
                    "contexts, so I cannot safely compare or revise them."
                ),
                memory_facts=map_facts,
                sensor_telemetry=telemetry,
            )

        if len({fact.object for fact in contextual_facts}) > 1:
            return EpistemicEvaluation(
                status="ambiguous",
                explanation=(
                    "Stored route claims disagree with each other in the observed context. "
                    "The live LiDAR result is available, but the map claim needs review."
                ),
                memory_facts=contextual_facts,
                sensor_telemetry=telemetry,
            )

        # Prefer the explicitly sourced static map claim when several sources agree.
        map_fact = next(
            (fact for fact in contextual_facts if fact.source_agent == "static_map"),
            contextual_facts[-1] if contextual_facts else None,
        )

        if sensor_status not in ("clear", "blocked"):
            return EpistemicEvaluation(
                status="unverifiable",
                explanation="LiDAR did not return a recognized clear/blocked route status.",
                memory_facts=contextual_facts,
                sensor_telemetry=telemetry,
            )

        # A fresh, relevant LiDAR result revises a contradictory stored route belief.
        if map_fact and map_fact.object != sensor_status:
            dist_str = f" at {nearest_dist:.1f}cm" if nearest_dist is not None else ""

            # Execute atomic belief revision in Tier 1
            successor_assertion = FactAssertion(
                version="v1",
                subject=map_fact.subject,
                predicate="status_is",
                object=sensor_status,
                source_agent="lidar_sensor",
                confidence_score=lidar_obs.confidence_score,
                observed_at=lidar_obs.observed_at,
                context=lidar_obs.context,
                evidence={
                    "lidar_distance_cm": nearest_dist,
                    "sensor": "lidar",
                    "status": sensor_status,
                    "observation_id": str(lidar_obs.observation_id),
                },
            )

            revision_reason = (
                f"Live LiDAR reading detected physical obstacle{dist_str}"
                if sensor_status == "blocked"
                else "Live LiDAR reported that the route is clear"
            )
            revision = memory_repo.record_revision(
                old_fact_id=map_fact.fact_id,
                replacement=successor_assertion,
                reason=revision_reason,
                policy_rule="lidar_overrides_map",
                revised_at=self._clock(),
            )

            if map_fact.object == "clear" and sensor_status == "blocked":
                explanation = (
                    "No, my static mapping says it is clear, but my live LiDAR readings indicate "
                    f"a physical obstruction{dist_str} right now. I have downgraded my map "
                    "confidence and updated my belief graph."
                )
            else:
                explanation = (
                    f"My static mapping says the route is {map_fact.object}, but my live LiDAR "
                    f"readings indicate it is {sensor_status}{dist_str} right now. I recorded the "
                    "new sensor-backed belief and updated the active belief graph."
                )

            return EpistemicEvaluation(
                status="grounded_conflict_resolved",
                explanation=explanation,
                revisions=[revision],
                audit_events=[revision.audit_event],
                memory_facts=[revision.successor],
                sensor_telemetry=telemetry,
            )

        if map_fact and map_fact.object == sensor_status:
            if sensor_status == "clear":
                answer = "Yes, the route is clear. The stored route claim and live LiDAR agree."
            else:
                answer = "Route is blocked as recorded. The live LiDAR confirms the stored claim."
            return EpistemicEvaluation(
                status="verified",
                explanation=answer,
                memory_facts=memory_facts,
                sensor_telemetry=telemetry,
            )

        if map_fact is None:
            if sensor_status == "blocked":
                distance = f" at {nearest_dist:.1f}cm" if nearest_dist is not None else ""
                answer = (
                    f"Live LiDAR reports the route is blocked{distance}; "
                    "no stored map claim was found."
                )
            else:
                answer = "Live LiDAR reports the route is clear; no stored map claim was found."
            return EpistemicEvaluation(
                status="verified",
                explanation=answer,
                memory_facts=[],
                sensor_telemetry=telemetry,
            )

        return EpistemicEvaluation(
            status="unverifiable",
            explanation="The route could not be compared with a relevant stored status claim.",
            memory_facts=contextual_facts,
            sensor_telemetry=telemetry,
        )

    def _evaluate_perspectives(
        self,
        user_question: str,
        observations: list[SensorObservation],
        memory_repo: EpistemicMemoryInterface,
    ) -> EpistemicEvaluation:
        """Scenario B: isolate User, Egocentric (camera), and Historical perspectives."""
        telemetry = [obs.model_dump(mode="json") for obs in observations]

        # 1. User Perspective: Extract requested color/target from question
        match = re.search(r"\b(red|blue|green|yellow|brown|black|white)\b", user_question.lower())
        user_expected_color = match.group(1) if match else None

        # 2. Egocentric Perspective: What does the camera register right now?
        camera_obs = next((obs for obs in observations if obs.capability == "camera_detect"), None)
        egocentric_color = None
        lighting_condition = None
        observed_object_id = None

        if camera_obs:
            meas = camera_obs.measurements
            lighting_condition = camera_obs.context.extra_context.get("lighting")
            observed_object_id = meas.get("object_id")
            if "apparent_color" in meas:
                egocentric_color = meas["apparent_color"]
            elif "objects" in meas and meas["objects"]:
                observed_object_id = meas["objects"][0].get("object_id")
                egocentric_color = meas["objects"][0].get("apparent_color")

        # Query history only for the object ID returned by the live camera.
        historical_facts: list[StoredFact] = []
        if isinstance(observed_object_id, str) and observed_object_id:
            historical_facts = memory_repo.query_facts(
                FactQuery(subject=observed_object_id, active_only=False)
            )
        color_facts = [
            fact
            for fact in historical_facts
            if fact.predicate in ("perceived_color_is", "painted_color_is", "color_is")
        ]
        latest_color_fact = max(color_facts, key=lambda fact: fact.created_at, default=None)

        perspectives = {
            "user_perspective": (
                f"{user_expected_color.capitalize()} (user expects a {user_expected_color} target, "
                "as stated in the question)."
                if user_expected_color
                else "The question does not state an expected color."
            ),
            "egocentric_perspective": (
                f"{egocentric_color.capitalize()} (currently sensed by the camera"
                + (f" under {lighting_condition} lighting)." if lighting_condition else ").")
                if egocentric_color
                else "No camera color was returned."
            ),
            "historical_perspective": (
                f"{latest_color_fact.object.capitalize()} (latest logged color claim from "
                f"'{latest_color_fact.source_agent}', recorded "
                f"{latest_color_fact.created_at.isoformat()}."
                if latest_color_fact
                else (
                    f"No historical color record was found for {observed_object_id}."
                    if observed_object_id
                    else "No object ID was returned by the camera, so history was not queried."
                )
            ),
        }

        explanation = "Perspective breakdown:\n"
        explanation += f"1. User Perspective: {perspectives['user_perspective']}\n"
        explanation += f"2. Egocentric Perspective: {perspectives['egocentric_perspective']}\n"
        explanation += (
            f"3. Historical/Third-Party Perspective: {perspectives['historical_perspective']}"
        )

        return EpistemicEvaluation(
            status="perspectives_tracked",
            explanation=explanation,
            perspectives=perspectives,
            memory_facts=historical_facts,
            sensor_telemetry=telemetry,
        )


def _contexts_match(fact_context: Any, observation_context: Any) -> bool:
    """Require overlapping place and frame data to agree before comparison."""
    for field_name in ("location", "frame_of_reference"):
        fact_value = getattr(fact_context, field_name)
        observation_value = getattr(observation_context, field_name)
        if (
            fact_value is not None
            and observation_value is not None
            and fact_value != observation_value
        ):
            return False
    return True
