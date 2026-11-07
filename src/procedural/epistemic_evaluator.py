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
    StoredObservationSample,
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
        *,
        revise_routes: bool = True,
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

        # 2. Handle temporal object location queries (Issue #32)
        if getattr(plan_result, "temporal_results", None):
            return self._evaluate_temporal_object_query(
                plan_result=plan_result,
                user_question=user_question,
                memory_repo=memory_repo,
            )

        # 3. Check for unverifiable sensor state
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
                revise_routes=revise_routes,
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
            events_by_id: dict[object, AuditEvent] = {}
            for ar in plan_result.audit_results:
                for trail in ar.trails.values():
                    trails.append(trail)
                    for event in trail.events:
                        events_by_id[event.event_id] = event
            events = list(events_by_id.values())
            facts_by_id = {fact.fact_id: fact for trail in trails for fact in trail.facts}
            event_explanations: list[str] = []
            for event in events:
                output_ids = set(event.output_fact_ids)
                previous = next(
                    (
                        facts_by_id[fact_id]
                        for fact_id in event.input_fact_ids
                        if fact_id not in output_ids and fact_id in facts_by_id
                    ),
                    None,
                )
                current = next(
                    (
                        facts_by_id[fact_id]
                        for fact_id in event.output_fact_ids
                        if fact_id in facts_by_id
                    ),
                    None,
                )
                if previous and current:
                    event_explanations.append(
                        f"{current.subject} {current.predicate} changed from "
                        f"{previous.object} (source: {previous.source_agent}) to "
                        f"{current.object} (source: {current.source_agent}). "
                        f"Reason: {event.reason} Policy: {event.policy_rule}."
                    )
                else:
                    event_explanations.append(f"{event.reason} Policy: {event.policy_rule}.")
            explanation = (
                "Audit history: " + " ".join(event_explanations)
                if event_explanations
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
        revise_routes: bool = True,
    ) -> EpistemicEvaluation:
        """Scenario A: compare active map belief with live LiDAR and revise if conflicted."""
        measurements = lidar_obs.measurements
        sensor_status = measurements.get("status")
        nearest_dist = measurements.get("nearest_distance_cm")
        telemetry = [lidar_obs.model_dump(mode="json")]

        # Warehouse ingestion owns route beliefs and scopes them to a run.
        # This answer describes the current scan independently of legacy map facts.
        if not revise_routes:
            if sensor_status not in ("clear", "blocked"):
                return EpistemicEvaluation(
                    status="unverifiable",
                    explanation="LiDAR did not return a recognized clear/blocked route status.",
                    sensor_telemetry=telemetry,
                )
            return EpistemicEvaluation(
                status="grounded",
                explanation=f"The forward LiDAR scan reported {sensor_status}.",
                sensor_telemetry=telemetry,
            )

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
                f"{latest_color_fact.created_at.isoformat()})."
                if latest_color_fact
                else (
                    f"No historical color record was found for {observed_object_id}."
                    if observed_object_id
                    else "No object ID was returned by the camera, so history was not queried."
                )
            ),
        }

        explanation = (
            "The available evidence gives three distinct perspectives:\n\n"
            f"- **Your expectation:** {perspectives['user_perspective']}\n"
            f"- **Current camera view:** {perspectives['egocentric_perspective']}\n"
            f"- **Historical record:** {perspectives['historical_perspective']}\n\n"
            "The historical record is a past claim; it does not by itself establish "
            "the object's current color."
        )

        return EpistemicEvaluation(
            status="perspectives_tracked",
            explanation=explanation,
            perspectives=perspectives,
            memory_facts=historical_facts,
            sensor_telemetry=telemetry,
        )

    def _evaluate_temporal_object_query(
        self,
        plan_result: PlanExecutionResult,
        user_question: str,
        memory_repo: EpistemicMemoryInterface,
    ) -> EpistemicEvaluation:
        """Evaluate observation ledger and temporal belief results for an object location query."""
        temporal_results = plan_result.temporal_results
        intent = plan_result.intent
        mode = intent.mode if intent and intent.mode else None
        entity_id = (
            intent.entity_mentions[0]
            if intent and intent.entity_mentions
            else (temporal_results[0].entity_id or "")
        )

        if mode is None:
            if any(tr.at_sim_time_ms is not None for tr in temporal_results):
                mode = "at_time"
            elif any(tr.mode == "history" for tr in temporal_results):
                mode = "changes" if plan_result.audit_results else "history"
            else:
                lq = user_question.lower()
                mode = "last_seen" if ("last" in lq or "when" in lq) else "current"

        at_sim_time_ms = (
            intent.at_sim_time_ms
            if intent and intent.at_sim_time_ms is not None
            else next(
                (tr.at_sim_time_ms for tr in temporal_results if tr.at_sim_time_ms is not None),
                None,
            )
        )

        sighting_samples: list[StoredObservationSample] = []
        latest_cam_samples: list[StoredObservationSample] = []
        for tr in temporal_results:
            if tr.mode in ("last_seen", "history"):
                sighting_samples.extend(tr.samples)
            elif tr.mode == "latest" and tr.capability == "camera_detect":
                latest_cam_samples.extend(tr.samples)

        all_samples = [s for tr in temporal_results for s in tr.samples]
        telemetry = [
            s.result.model_dump(mode="json")
            if isinstance(s.result, SensorObservation)
            else {
                "sample_id": str(s.sample_id),
                "capability": s.request.capability,
                "status": "unavailable",
                "reason": s.result.reason,
            }
            for s in all_samples
        ]
        memory_facts = [f for m in plan_result.memory_results for f in m.facts]
        trails = [trail for ar in plan_result.audit_results for trail in ar.trails.values()]
        events = [e for trail in trails for e in trail.events]

        latest_camera = latest_cam_samples[0] if latest_cam_samples else None

        def _fmt_coord(val: float | int | None) -> str:
            if val is None:
                return "0"
            if isinstance(val, (int, float)) and float(val).is_integer():
                return str(int(val))
            return f"{val:.1f}"

        def _find_det(
            sample: StoredObservationSample, target: str
        ) -> tuple[float, float, str, str] | None:
            if (
                not isinstance(sample.result, SensorObservation)
                or sample.result.capability != "camera_detect"
            ):
                return None
            measurements = sample.result.measurements
            objects: list[Any] = []
            if isinstance(measurements, dict):
                objects = measurements.get("objects", [])
            elif hasattr(measurements, "objects"):
                objects = getattr(measurements, "objects", [])

            for obj in objects:
                if isinstance(obj, dict) and obj.get("object_id") == target:
                    pos = obj.get("position", {})
                    return (
                        float(pos.get("x_cm", 0.0)),
                        float(pos.get("y_cm", 0.0)),
                        str(obj.get("apparent_color", "")),
                        str(obj.get("frame_of_reference", "")),
                    )
                elif hasattr(obj, "object_id") and getattr(obj, "object_id") == target:
                    pos = getattr(obj, "position")
                    return (
                        float(getattr(pos, "x_cm", 0.0)),
                        float(getattr(pos, "y_cm", 0.0)),
                        str(getattr(obj, "apparent_color", "")),
                        str(getattr(obj, "frame_of_reference", "")),
                    )
            return None

        # 1. Mode: current
        if mode == "current":
            if sighting_samples:
                sighting = sighting_samples[0]
                det = _find_det(sighting, entity_id)
                x_cm = _fmt_coord(det[0] if det else 0.0)
                y_cm = _fmt_coord(det[1] if det else 0.0)
                obs_pose = sighting.observer_pose

                if latest_camera and latest_camera.sim_time_ms > sighting.sim_time_ms:
                    cam_ms = latest_camera.sim_time_ms
                    if isinstance(latest_camera.result, ObservationUnavailable):
                        reason = latest_camera.result.reason
                        return EpistemicEvaluation(
                            status="uncertain",
                            explanation=(
                                f"Current location of {entity_id} cannot be freshly verified "
                                f"because the camera reported an outage at simulation time "
                                f"{cam_ms} ms ({reason}). Last confirmed sighting was at "
                                f"simulation time {sighting.sim_time_ms} ms at x={x_cm} cm, "
                                f"y={y_cm} cm in {obs_pose.location} by {obs_pose.robot_id} "
                                f"(sample ID: {sighting.sample_id})."
                            ),
                            memory_facts=memory_facts,
                            sensor_telemetry=telemetry,
                        )
                    else:
                        later_det = _find_det(latest_camera, entity_id)
                        if later_det is None:
                            return EpistemicEvaluation(
                                status="uncertain",
                                explanation=(
                                    f"Current location of {entity_id} cannot be freshly verified "
                                    f"because a subsequent camera scan at simulation time "
                                    f"{cam_ms} ms did not detect it in "
                                    f"{latest_camera.observer_pose.location}. Last confirmed "
                                    f"sighting was at simulation time {sighting.sim_time_ms} ms "
                                    f"at x={x_cm} cm, y={y_cm} cm in {obs_pose.location} "
                                    f"by {obs_pose.robot_id} (sample ID: {sighting.sample_id})."
                                ),
                                memory_facts=memory_facts,
                                sensor_telemetry=telemetry,
                            )
                        else:
                            x_cm = _fmt_coord(later_det[0])
                            y_cm = _fmt_coord(later_det[1])
                            obs_pose = latest_camera.observer_pose
                            sighting = latest_camera

                return EpistemicEvaluation(
                    status="verified",
                    explanation=(
                        f"{entity_id} was observed at x={x_cm} cm, y={y_cm} cm in "
                        f"{obs_pose.location} by {obs_pose.robot_id} at simulation time "
                        f"{sighting.sim_time_ms} ms (sample ID: {sighting.sample_id})."
                    ),
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )
            else:
                loc_fact = next(
                    (
                        f
                        for f in memory_facts
                        if f.predicate == "located_at" and f.superseded_by is None
                    ),
                    None,
                )
                if loc_fact:
                    return EpistemicEvaluation(
                        status="stored_belief",
                        explanation=(
                            f"No physical camera observations have been recorded for "
                            f"{entity_id} in this run. Stored memory record indicates "
                            f"{entity_id} is located at {loc_fact.object} "
                            f"(source: {loc_fact.source_agent}), but current physical state "
                            f"is unverified."
                        ),
                        memory_facts=memory_facts,
                        sensor_telemetry=telemetry,
                    )
                return EpistemicEvaluation(
                    status="no_data",
                    explanation=f"No observations or memory records found for {entity_id}.",
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )

        # 2. Mode: last_seen
        if mode == "last_seen":
            if sighting_samples:
                sighting = sighting_samples[0]
                det = _find_det(sighting, entity_id)
                x_cm = _fmt_coord(det[0] if det else 0.0)
                y_cm = _fmt_coord(det[1] if det else 0.0)
                obs_pose = sighting.observer_pose

                latest_ms = latest_camera.sim_time_ms if latest_camera else sighting.sim_time_ms
                age_ms = max(0, latest_ms - sighting.sim_time_ms)

                subsequent_ctx = "No subsequent conflicting camera scans recorded."
                if latest_camera and latest_camera.sim_time_ms > sighting.sim_time_ms:
                    if isinstance(latest_camera.result, ObservationUnavailable):
                        subsequent_ctx = (
                            f"Subsequent camera scan at {latest_camera.sim_time_ms} ms reported "
                            f"outage ({latest_camera.result.reason})."
                        )
                    else:
                        subsequent_ctx = (
                            f"Subsequent camera scan at {latest_camera.sim_time_ms} ms did not "
                            f"detect {entity_id}."
                        )

                return EpistemicEvaluation(
                    status="verified",
                    explanation=(
                        f"{entity_id} was last seen at simulation time {sighting.sim_time_ms} ms "
                        f"at x={x_cm} cm, y={y_cm} cm in {obs_pose.location} by "
                        f"{obs_pose.robot_id} (sample ID: {sighting.sample_id}, "
                        f"age: {age_ms} ms). Current verification status: {subsequent_ctx}"
                    ),
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )
            return EpistemicEvaluation(
                status="no_data",
                explanation=(
                    f"No positive sightings of {entity_id} have been recorded in the "
                    f"observation ledger."
                ),
                memory_facts=memory_facts,
                sensor_telemetry=telemetry,
            )

        # 3. Mode: at_time
        if mode == "at_time":
            target_ms = at_sim_time_ms if at_sim_time_ms is not None else 0
            latest_run_ms = latest_camera.sim_time_ms if latest_camera else None
            if latest_run_ms is None and sighting_samples:
                latest_run_ms = sighting_samples[0].sim_time_ms

            if latest_run_ms is not None and target_ms > latest_run_ms:
                return EpistemicEvaluation(
                    status="uncertain",
                    explanation=(
                        f"The requested simulation time {target_ms} ms is in the future "
                        f"relative to available observation ledger evidence "
                        f"(latest recorded: {latest_run_ms} ms). Location cannot be verified."
                    ),
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )

            if sighting_samples:
                sighting = sighting_samples[0]
                det = _find_det(sighting, entity_id)
                x_cm = _fmt_coord(det[0] if det else 0.0)
                y_cm = _fmt_coord(det[1] if det else 0.0)
                obs_pose = sighting.observer_pose
                age_ms = target_ms - sighting.sim_time_ms

                return EpistemicEvaluation(
                    status="verified",
                    explanation=(
                        f"At simulation time {target_ms} ms, the latest applicable observation "
                        f"of {entity_id} was captured at {sighting.sim_time_ms} ms "
                        f"(age: {age_ms} ms) at x={x_cm} cm, y={y_cm} cm in {obs_pose.location} "
                        f"by {obs_pose.robot_id} (sample ID: {sighting.sample_id}). "
                        f"Note: No intermediate position is interpolated at {target_ms} ms."
                    ),
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )

            return EpistemicEvaluation(
                status="no_data",
                explanation=(
                    f"No observations of {entity_id} were recorded at or before simulation "
                    f"time {target_ms} ms."
                ),
                memory_facts=memory_facts,
                sensor_telemetry=telemetry,
            )

        # 4. Mode: history
        if mode == "history":
            if sighting_samples:
                timeline = []
                for s in sighting_samples:
                    det = _find_det(s, entity_id)
                    x_cm = _fmt_coord(det[0] if det else 0.0)
                    y_cm = _fmt_coord(det[1] if det else 0.0)
                    timeline.append(
                        f"[{s.sim_time_ms} ms: at x={x_cm} cm, y={y_cm} cm in "
                        f"{s.observer_pose.location} by {s.observer_pose.robot_id} "
                        f"(sample ID: {s.sample_id})]"
                    )
                return EpistemicEvaluation(
                    status="verified",
                    explanation=f"Sighting timeline for {entity_id}: " + "; ".join(timeline) + ".",
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )
            return EpistemicEvaluation(
                status="no_data",
                explanation=(
                    f"No sighting history recorded for {entity_id} in the observation ledger."
                ),
                memory_facts=memory_facts,
                sensor_telemetry=telemetry,
            )

        # 5. Mode: changes
        if mode == "changes":
            if not sighting_samples:
                return EpistemicEvaluation(
                    status="no_data",
                    explanation=f"No sighting history or relocation changes found for {entity_id}.",
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                )

            transitions = []
            for i in range(len(sighting_samples) - 1):
                s1 = sighting_samples[i]
                s2 = sighting_samples[i + 1]
                det1 = _find_det(s1, entity_id)
                det2 = _find_det(s2, entity_id)
                pos1 = (round(det1[0], 1), round(det1[1], 1)) if det1 else (0.0, 0.0)
                pos2 = (round(det2[0], 1), round(det2[1], 1)) if det2 else (0.0, 0.0)
                loc1 = s1.observer_pose.location
                loc2 = s2.observer_pose.location
                if loc1 != loc2 or pos1 != pos2:
                    transitions.append((s1, s2, loc1, loc2))

            if transitions:
                trans_texts = []
                for s1, s2, loc1, loc2 in transitions:
                    trans_texts.append(
                        f"sighted at {loc1} at {s1.sim_time_ms} ms (sample ID: {s1.sample_id}), "
                        f"subsequently sighted at {loc2} at {s2.sim_time_ms} ms "
                        f"(sample ID: {s2.sample_id})"
                    )
                explanation = (
                    f"Observed transition for {entity_id}: " + "; ".join(trans_texts) + "."
                )

                audit_texts = []
                for ev in events:
                    audit_texts.append(f"Revision record: {ev.reason} (Policy: {ev.policy_rule}).")
                if audit_texts:
                    explanation += " " + " ".join(audit_texts)

                explanation += (
                    " Note: Two sightings at distinct locations demonstrate that a transition "
                    "occurred, but do not prove continuous path, exact transit duration, or "
                    "physical relocation cause."
                )
                return EpistemicEvaluation(
                    status="verified",
                    explanation=explanation,
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                    audit_trails=trails,
                    audit_events=events,
                )
            else:
                s_first = sighting_samples[0]
                s_last = sighting_samples[-1]
                det = _find_det(s_first, entity_id)
                x_cm = _fmt_coord(det[0] if det else 0.0)
                y_cm = _fmt_coord(det[1] if det else 0.0)
                return EpistemicEvaluation(
                    status="verified",
                    explanation=(
                        f"No location change observed for {entity_id}. Sighted at x={x_cm} cm, "
                        f"y={y_cm} cm in {s_first.observer_pose.location} across "
                        f"{len(sighting_samples)} observation(s) from "
                        f"{s_first.sim_time_ms} ms to {s_last.sim_time_ms} ms."
                    ),
                    memory_facts=memory_facts,
                    sensor_telemetry=telemetry,
                    audit_trails=trails,
                    audit_events=events,
                )

        return EpistemicEvaluation(
            status="unsupported",
            explanation=f"Unsupported temporal query mode: {mode}",
            memory_facts=memory_facts,
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
