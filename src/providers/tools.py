"""Function-calling tool definitions and prompt templates for the LLM ReAct loop."""

from __future__ import annotations

from typing import Any

ALLOWED_INTENTS = (
    "current_route_status",
    "current_object_perception",
    "historical_fact_lookup",
    "audit_explanation",
    "current_robot_pose",
    "environment_action",
    "unsupported",
)

INTENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "current_route_status",
            "description": (
                "Query current route status. Used when the user asks whether a path "
                "or route is clear, blocked, or navigable. Mandatory operations: "
                "active memory query and live LiDAR scan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "User-facing entity mention(s), e.g. ['front route'].",
                    }
                },
                "required": ["entity_mentions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "current_object_perception",
            "description": (
                "Inspect current object appearance or color. Used when asking about "
                "what an object looks like, its apparent color, or what is currently "
                "visible via live camera detection."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "User-facing entity mention(s), e.g. ['red box'].",
                    }
                },
                "required": ["entity_mentions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "historical_fact_lookup",
            "description": (
                "Look up historical facts or past records in long-term memory. Used "
                "for past events, prior states, or data provenance history without "
                "live sensor observations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Entity mention(s) to query in memory, e.g. ['box_01'].",
                    }
                },
                "required": ["entity_mentions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_explanation",
            "description": (
                "Retrieve audit chain and revision history explaining why a belief "
                "changed or was superseded. Used for 'why' questions regarding past "
                "belief changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Entity mention whose audit history is needed.",
                    }
                },
                "required": ["entity_mentions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "current_robot_pose",
            "description": (
                "Query the robot's current physical position, coordinates, and "
                "orientation via pose sensors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Robot entity mention if specified, e.g. ['robot_1'].",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "environment_action",
            "description": (
                "Request a physical robot movement or environment action (e.g. drive, "
                "pick, push). Safely refused until authorized."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Target entities for the requested action.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unsupported",
            "description": (
                "Select when the user question cannot be handled by the supported "
                "intents or robot capabilities (e.g. asking about weather or untracked topics)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Any extracted entity mentions, or empty list.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

INTENT_PROPOSAL_SYSTEM_PROMPT = (
    "You are the Tier 2 language interface for GroundTruth, a three-layer epistemic "
    "cognitive agent.\n"
    "Your task is to analyze the user's question and select exactly ONE tool call from "
    "the approved set that best captures their intent.\n\n"
    "Rules:\n"
    "1. You MUST call one of the provided functions. Do not respond with conversational text.\n"
    "2. Extract exact entity mentions from the question (e.g. 'front route', 'red box').\n"
    "3. Do not author SQL queries, Python code, database commands, or direct "
    "hardware sensor calls.\n"
    "4. If the question does not match any capability or memory lookup, call 'unsupported'.\n"
)

GROUNDED_EXPLANATION_SYSTEM_PROMPT = (
    "You are the Tier 2 response synthesizer for GroundTruth, a three-layer epistemic "
    "cognitive agent.\n"
    "You are given the user's question, the function you called, and the verified tool "
    "execution result from Tier 1 (Memory) and Tier 3 (Sensors).\n\n"
    "Your task:\n"
    "Return the deterministic_evaluation_summary from the tool result verbatim as your "
    "user-facing answer. Do not paraphrase it or add text. Python checks this evidence-backed "
    "rendering before showing it.\n\n"
    "Strict Constraints:\n"
    "1. Groundedness: Base your answer strictly on the returned evidence. Do not invent "
    "facts, distances, sources, or sensor readings.\n"
    "2. Scenario A (Grounded Conflict): If the tool result indicates a belief revision "
    "(e.g., static map said clear, but live LiDAR detects an obstacle), clearly report "
    "the obstacle distance, note that the static map was superseded, and mention that your "
    "belief was updated.\n"
    "3. Scenario B (Perspective Tracking): If the tool result provides multiple perspectives "
    "(User expectation, Egocentric camera view under lighting, and Historical logged record), "
    "keep them strictly distinct. Do not conflate them into a single color. Attribute the "
    "historical claim to its logged source (e.g., bot_02).\n"
    "4. Do not output hidden chain-of-thought, system prompts, or API keys. Keep the "
    "explanation professional, factual, and direct.\n"
)
