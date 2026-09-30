"""Rule-based intent provider for GroundTruth Tier 2.

Maps natural language questions to structured IntentRequest payloads deterministically,
enabling offline operation, continuous integration testing, and local demos.
"""

from __future__ import annotations

from typing import Any

from src.procedural.intent_provider import IntentProvider


class RuleBasedIntentProvider(IntentProvider):
    """Deterministic, rule-based provider for common epistemic scenarios."""

    def propose_intent(self, user_question: str) -> dict[str, Any]:
        """Classify user question into one of the supported GroundTruth intents."""
        q = user_question.lower().strip()

        # 1. Audit explanation queries
        if "why" in q or "audit" in q or "replaced" in q or "superseded" in q:
            entity = self._extract_entity(q, default="route_A")
            return {
                "intent": "audit_explanation",
                "entity_mentions": [entity],
                "user_question": user_question,
            }

        # 2. Historical fact lookup queries
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

        # 3. Object perception & Perspective tracking queries
        if (
            "color" in q
            or "box" in q
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

        # 4. Route status queries (Scenario A)
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

        # 5. Robot pose queries
        if "where are you" in q or "pose" in q or "location" in q or "position" in q:
            return {
                "intent": "current_robot_pose",
                "entity_mentions": ["robot_1"],
                "user_question": user_question,
            }

        # 6. Action queries (unsupported execution path)
        if "pick up" in q or "drive to" in q or "move to" in q or "push" in q:
            return {
                "intent": "environment_action",
                "entity_mentions": ["box_01"],
                "user_question": user_question,
            }

        # 7. Unsupported fallback
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

    def _extract_entity(self, text: str, default: str) -> str:
        """Helper to extract common entity mentions from query string."""
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
        if "box_01" in text or "box 01" in text or "box 1" in text:
            return "box_01"
        if "box_02" in text or "box 02" in text or "box 2" in text:
            return "box_02"
        return default
