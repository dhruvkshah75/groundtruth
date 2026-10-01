"""Groq-backed function-calling LLM adapter for GroundTruth Tier 2."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from src.procedural.intent_provider import IntentProviderUnavailableError
from src.providers.tools import (
    ALLOWED_INTENTS,
    GROUNDED_EXPLANATION_SYSTEM_PROMPT,
    INTENT_PROPOSAL_SYSTEM_PROMPT,
    INTENT_TOOLS,
)

LOGGER = logging.getLogger("groundtruth.providers.groq")


@runtime_checkable
class GroqClientInterface(Protocol):
    """Injectable client interface for Groq chat completions."""

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float = 0.0,
    ) -> Any: ...


class RealGroqClient:
    """Production Groq client wrapping the official groq SDK."""

    def __init__(self, api_key: str) -> None:
        from groq import Groq

        self._client = Groq(api_key=api_key)

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float = 0.0,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        return self._client.chat.completions.create(**kwargs)


class GroqIntentProvider:
    """Real LLM intent provider implementing function-calling ReAct cycles.

    1. Translates natural language questions to structured function calls.
    2. Supports bounded 1-attempt repair and unsupported intent reconsideration.
    3. Synthesizes final grounded explanations using actual Tier 1 & Tier 3 tool results.
    """

    def __init__(
        self,
        client: GroqClientInterface,
        model: str = "llama-3.3-70b-versatile",
    ) -> None:
        self._client = client
        self._model = model
        self.last_tool_call_id: str = "call_init"
        self.last_tool_name: str = ""
        self.last_tool_args: dict[str, Any] = {}
        self.last_trace: dict[str, Any] = {}

    @property
    def model_name(self) -> str:
        return self._model

    def propose_intent(self, user_question: str) -> dict[str, Any]:
        """Propose an intent via function calling."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": INTENT_PROPOSAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_question},
        ]
        try:
            response = self._client.create_chat_completion(
                model=self._model,
                messages=messages,
                tools=INTENT_TOOLS,
                tool_choice="required",
                temperature=0.0,
            )
        except Exception as exc:
            LOGGER.exception("Groq API error during propose_intent")
            raise IntentProviderUnavailableError(f"Groq API call failed: {exc}") from exc

        return self._extract_tool_call_payload(response, user_question)

    def repair_intent(self, user_question: str, validation_error: str) -> dict[str, Any]:
        """Attempt to correct an invalid proposal by supplying the error feedback."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": INTENT_PROPOSAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_question},
            {
                "role": "user",
                "content": (
                    f"Your previous function call had a validation error: {validation_error}. "
                    "Select one approved function from the schema with correct arguments."
                ),
            },
        ]
        try:
            response = self._client.create_chat_completion(
                model=self._model,
                messages=messages,
                tools=INTENT_TOOLS,
                tool_choice="required",
                temperature=0.0,
            )
        except Exception as exc:
            LOGGER.exception("Groq API error during repair_intent")
            raise IntentProviderUnavailableError(f"Groq repair call failed: {exc}") from exc

        return self._extract_tool_call_payload(response, user_question)

    def reconsider_unsupported(
        self, user_question: str, candidate_intents: tuple[str, ...]
    ) -> dict[str, Any]:
        """Reconsider an unsupported proposal within constrained candidate choices."""
        filtered_tools = [
            tool for tool in INTENT_TOOLS if tool["function"]["name"] in candidate_intents
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": INTENT_PROPOSAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_question},
            {
                "role": "user",
                "content": (
                    f"Candidate functions: {list(candidate_intents)}. "
                    "Select the matching function or call 'unsupported'."
                ),
            },
        ]
        try:
            response = self._client.create_chat_completion(
                model=self._model,
                messages=messages,
                tools=filtered_tools,
                tool_choice="required",
                temperature=0.0,
            )
        except Exception as exc:
            LOGGER.exception("Groq API error during reconsider_unsupported")
            raise IntentProviderUnavailableError(
                f"Groq reconsideration call failed: {exc}"
            ) from exc

        return self._extract_tool_call_payload(response, user_question)

    def generate_grounded_explanation(
        self,
        user_question: str,
        tool_name: str,
        tool_call_id: str,
        tool_result: dict[str, Any],
        fallback_explanation: str,
    ) -> str:
        """Send execution results back to LLM as tool output and return grounded response."""
        result_str = json.dumps(tool_result, ensure_ascii=False)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": GROUNDED_EXPLANATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_question},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id or "call_default",
                        "type": "function",
                        "function": {
                            "name": tool_name or "current_route_status",
                            "arguments": json.dumps(self.last_tool_args or {}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call_id or "call_default",
                "name": tool_name or "current_route_status",
                "content": result_str,
            },
        ]

        try:
            response = self._client.create_chat_completion(
                model=self._model,
                messages=messages,
                temperature=0.0,
            )
            content = self._extract_message_content(response)
            if content and content.strip():
                return content.strip()
        except Exception as exc:
            LOGGER.warning(
                "Grounded explanation generation failed, using deterministic fallback: %s", exc
            )

        return fallback_explanation

    def _extract_tool_call_payload(self, response: Any, user_question: str) -> dict[str, Any]:
        """Parse function-calling response into IntentRequest dictionary format."""
        try:
            # Handle both SDK object response and mock dict response
            choices = getattr(response, "choices", None)
            if choices is None and isinstance(response, dict):
                choices = response.get("choices", [])

            if not choices:
                return {
                    "intent": "unsupported",
                    "entity_mentions": [],
                    "user_question": user_question,
                }

            message = getattr(choices[0], "message", None)
            if message is None and isinstance(choices[0], dict):
                message = choices[0].get("message", {})

            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls is None and isinstance(message, dict):
                tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # Model returned regular text instead of a tool call
                return {
                    "intent": "unsupported",
                    "entity_mentions": [],
                    "user_question": user_question,
                }

            tool_call = tool_calls[0]
            function = getattr(tool_call, "function", None)
            if function is None and isinstance(tool_call, dict):
                function = tool_call.get("function", {})

            fn_name = getattr(function, "name", "")
            if not fn_name and isinstance(function, dict):
                fn_name = function.get("name", "")

            raw_args = getattr(function, "arguments", "{}")
            if not raw_args and isinstance(function, dict):
                raw_args = function.get("arguments", "{}")

            call_id = getattr(tool_call, "id", "call_1")
            if not call_id and isinstance(tool_call, dict):
                call_id = tool_call.get("id", "call_1")

            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            entity_mentions = (
                parsed_args.get("entity_mentions", []) if isinstance(parsed_args, dict) else []
            )

            # Save state for ReAct explanation round
            self.last_tool_call_id = str(call_id)
            self.last_tool_name = str(fn_name)
            self.last_tool_args = parsed_args if isinstance(parsed_args, dict) else {}
            self.last_trace = {
                "tool_call_id": self.last_tool_call_id,
                "tool_name": self.last_tool_name,
                "arguments": self.last_tool_args,
            }

            intent_val = fn_name if fn_name in ALLOWED_INTENTS else "unsupported"

            return {
                "intent": intent_val,
                "entity_mentions": [str(m) for m in entity_mentions],
                "user_question": user_question,
            }
        except Exception as exc:
            LOGGER.warning("Failed to extract tool call payload: %s", exc)
            return {"intent": "unsupported", "entity_mentions": [], "user_question": user_question}

    @staticmethod
    def _extract_message_content(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices", [])
        if not choices:
            return ""
        msg = getattr(choices[0], "message", None)
        if msg is None and isinstance(choices[0], dict):
            msg = choices[0].get("message", {})
        content = getattr(msg, "content", "")
        if not content and isinstance(msg, dict):
            content = msg.get("content", "")
        return str(content or "")
