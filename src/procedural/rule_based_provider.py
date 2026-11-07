"""Rule-based intent provider for GroundTruth Tier 2.

Maps natural language questions to structured IntentRequest payloads deterministically,
enabling offline operation, continuous integration testing, and local demos.
"""

from __future__ import annotations

import re
from typing import Any

from src.procedural.intent_provider import IntentProvider


class RuleBasedIntentProvider(IntentProvider):
    """Deterministic, rule-based provider for common epistemic scenarios."""

    def __init__(self) -> None:
        self.last_tool_name: str = ""
        self.last_tool_call_id: str = ""
        self.last_tool_args: dict[str, Any] = {}
        self.last_error: str | None = None
        self.model_name: str | None = "rule_based"

    def propose_intent(self, user_question: str) -> dict[str, Any]:
        """Classify user question into one of the supported GroundTruth intents."""
        proposal = self._classify_intent(user_question)
        self.last_tool_name = str(proposal.get("intent", ""))
        if not getattr(self, "last_tool_call_id", None):
            self.last_tool_call_id = "deterministic_0"
        self.last_tool_args = dict(proposal)
        return proposal

    def _classify_intent(self, user_question: str) -> dict[str, Any]:
        """Core rule-based classification."""
        q = user_question.lower().strip()

        # Combined user/camera/history questions need a live camera observation;
        # the evaluator then retrieves history for the observed object's ID.
        if "color" in q and any(term in q for term in ("user", "perspective", "register")):
            return {
                "intent": "current_object_perception",
                "entity_mentions": [self._extract_entity(q, default="red box")],
                "user_question": user_question,
            }

        # 0. Reject unsupported future prediction or control action requests
        if (
            "predict" in q
            or "tomorrow" in q
            or "future" in q
            or "navigate" in q
            or "go to" in q
            or "move to" in q
            or "pick up" in q
        ):
            return {
                "intent": "unsupported",
                "entity_mentions": [],
                "user_question": user_question,
            }

        # 1. Temporal object location queries (P2-08)
        # Questions asking where an object is, was, when it was seen, its history, or what changed
        is_obj = any(
            t in q
            for t in (
                "parcel",
                "box",
                "package",
                "item",
                "object",
            )
        ) or bool(re.search(r"\bit\b", q))
        is_loc_or_time = any(
            term in q
            for term in (
                "where",
                "when",
                "last see",
                "last seen",
                "last saw",
                "last observe",
                "last sighting",
                "what changed",
                "before and what",
                "location",
                "position",
                "moved",
                "history",
                "timeline",
                "sightings",
            )
        ) or bool(re.search(r"\bat\s+-?\d+", q))

        if (
            is_obj
            and is_loc_or_time
            and not (
                "where are you" in q
                or "your position" in q
                or "your location" in q
                or "your pose" in q
                or "route" in q
            )
        ):
            entity = self._extract_entity(q, default=None)
            mentions = [entity] if entity is not None else []

            # A. changes mode: "what changed", "before and what changed", "moved"
            if (
                "what changed" in q
                or "before and what" in q
                or "did it change" in q
                or "moved" in q
                or "relocated" in q
            ):
                return {
                    "intent": "object_location_query",
                    "mode": "changes",
                    "entity_mentions": mentions,
                    "user_question": user_question,
                }

            # B. last_seen mode: "when did you last see", "last seen", "last sighting"
            if (
                "last see" in q
                or "last seen" in q
                or "last saw" in q
                or "last sighting" in q
                or "last observe" in q
                or "last observed" in q
                or "when did you" in q
                or "when was" in q
            ):
                return {
                    "intent": "object_location_query",
                    "mode": "last_seen",
                    "entity_mentions": mentions,
                    "user_question": user_question,
                }

            # C. at_time mode: "where was parcel_01 at 7 seconds", "at 7s", "at 7000ms"
            time_match = re.search(
                r"\bat\s+(-?\d+(?:\.\d+)?)\s*(seconds?|secs?|s|milliseconds?|ms)?\b", q
            )
            if time_match or "at time" in q or "at second" in q:
                at_sim_time_ms = None
                if time_match:
                    val_str = time_match.group(1)
                    unit = (time_match.group(2) or "").lower()
                    try:
                        val = float(val_str)
                        if unit in ("ms", "millisecond", "milliseconds"):
                            at_sim_time_ms = int(val)
                        else:
                            at_sim_time_ms = int(val * 1000)
                    except ValueError:
                        at_sim_time_ms = None

                return {
                    "intent": "object_location_query",
                    "mode": "at_time",
                    "at_sim_time_ms": at_sim_time_ms,
                    "entity_mentions": mentions,
                    "user_question": user_question,
                }

            # D. history mode: "sighting history", "history of parcel_01"
            if "history" in q or "timeline" in q or "sightings" in q:
                return {
                    "intent": "object_location_query",
                    "mode": "history",
                    "entity_mentions": mentions,
                    "user_question": user_question,
                }

            # E. current mode: "where is parcel_01 now", "location of parcel_01"
            return {
                "intent": "object_location_query",
                "mode": "current",
                "entity_mentions": mentions,
                "user_question": user_question,
            }

        # 2. Audit explanation queries
        if "why" in q or "audit" in q or "replaced" in q or "superseded" in q:
            entity = self._extract_entity(q, default="route_A")
            return {
                "intent": "audit_explanation",
                "entity_mentions": [entity],
                "user_question": user_question,
            }

        # 3. Historical fact lookup queries
        if (
            "history" in q
            or "what did the map say" in q
            or "earlier" in q
            or "past" in q
            or "provenance" in q
            or "what was recorded" in q
        ):
            entity = self._extract_entity(q, default="route_A")
            return {
                "intent": "historical_fact_lookup",
                "entity_mentions": [entity],
                "user_question": user_question,
            }

        # 4. Object perception & Perspective tracking queries
        if (
            "color" in q
            or "look like" in q
            or "see" in q
            or "camera" in q
            or "perspective" in q
            or "apparent" in q
        ):
            entity = self._extract_entity(q, default="red box")
            return {
                "intent": "current_object_perception",
                "entity_mentions": [entity],
                "user_question": user_question,
            }

        # 5. Route status queries (Scenario A)
        if (
            "route" in q
            or "path" in q
            or "clear" in q
            or "blocked" in q
            or "move forward" in q
            or "obstacle" in q
            or "lidar" in q
            or "ahead" in q
        ):
            entity = self._extract_entity(q, default="front route")
            return {
                "intent": "current_route_status",
                "entity_mentions": [entity],
                "user_question": user_question,
            }

        # 6. Robot pose queries
        if "where are you" in q or "pose" in q or "location" in q or "position" in q:
            return {
                "intent": "current_robot_pose",
                "entity_mentions": ["robot_1"],
                "user_question": user_question,
            }

        # 7. Action queries (unsupported execution path)
        if "pick up" in q or "drive to" in q or "move to" in q or "push" in q:
            return {
                "intent": "environment_action",
                "entity_mentions": ["box_01"],
                "user_question": user_question,
            }

        # 8. Unsupported fallback
        return {
            "intent": "unsupported",
            "entity_mentions": [],
            "user_question": user_question,
        }

    def repair_intent(self, user_question: str, validation_error: str) -> dict[str, Any]:
        """Attempt to repair by falling back to proposing a valid intent."""
        return self.propose_intent(user_question)

    def reconsider_unsupported(
        self, user_question: str, candidate_intents: tuple[str, ...]
    ) -> dict[str, Any]:
        """Pick a candidate if possible, or stay unsupported."""
        for candidate in candidate_intents:
            if candidate != "unsupported":
                return {
                    "intent": candidate,
                    "entity_mentions": ["route_A"],
                    "user_question": user_question,
                }
        return {
            "intent": "unsupported",
            "entity_mentions": [],
            "user_question": user_question,
        }

    def _extract_entity(self, text: str, default: str | None = None) -> str | None:
        """Helper to extract common entity mentions from query string."""
        # Check known color/article prefixes with parcel/box/route first
        m_phrase = re.search(
            r"\b((?:red|blue|green|yellow|brown|the|front)\s+(?:parcel|box|route|item|package))\b",
            text,
        )
        if m_phrase:
            return m_phrase.group(1)

        if "front route" in text:
            return "front route"
        if "route a" in text:
            return "route A"
        if "route_a" in text:
            return "route_A"
        if "route b" in text or "route_b" in text:
            return "route_B"
        if "blue box" in text:
            return "blue box"
        if "red box" in text:
            return "red box"
        if "brown box" in text:
            return "brown box"
        if "the box" in text:
            return "the box"
        if "the parcel" in text:
            return "the parcel"
        if "the item" in text:
            return "the item"
        if "the object" in text:
            return "the object"
        if "the package" in text:
            return "the package"
        if "parcel_handoff" in text or "parcel handoff" in text:
            return "parcel_handoff"

        # Regex for parcel_XX or box_XX with digits
        m_digit = re.search(r"\b(parcel[_\s]?\d+|box[_\s]?\d+)\b", text)
        if m_digit:
            return m_digit.group(1).replace(" ", "_")

        # Regex for parcel_XX or box_XX
        m = re.search(r"\b(parcel[_\s]?\w+|box[_\s]?\w+)\b", text)
        if m:
            val = m.group(1).replace(" ", "_")
            if val not in (
                "parcel_now",
                "parcel_before",
                "parcel_located",
                "parcel_right",
                "parcel_right_now",
            ):
                return val

        # Check for pronouns or generic references like "it"
        m_it = re.search(r"\b(it)\b", text)
        if m_it:
            return "it"

        return default
