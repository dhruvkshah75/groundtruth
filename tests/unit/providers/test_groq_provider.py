"""Unit tests for the Groq LLM function-calling provider and configuration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.procedural.intent_provider import IntentProviderUnavailableError
from src.procedural.rule_based_provider import RuleBasedIntentProvider
from src.providers import (
    GroqIntentProvider,
    ProviderConfig,
    UnconfiguredGroqProvider,
    create_provider,
)


class FakeChatCompletionMessage:
    def __init__(self, tool_calls: list[Any] | None = None, content: str | None = None) -> None:
        self.tool_calls = tool_calls
        self.content = content


class FakeChatChoice:
    def __init__(self, message: FakeChatCompletionMessage) -> None:
        self.message = message


class FakeChatCompletion:
    def __init__(self, choices: list[FakeChatChoice]) -> None:
        self.choices = choices


class FakeToolCall:
    def __init__(self, name: str, arguments: str, call_id: str = "call_test_123") -> None:
        self.id = call_id
        self.function = MagicMock(name=name, arguments=arguments)
        self.function.name = name
        self.function.arguments = arguments


class FakeGroqClient:
    """Offline test double for GroqClientInterface."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float = 0.0,
    ) -> Any:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise RuntimeError("No remaining mock responses in FakeGroqClient")
        next_resp = self.responses.pop(0)
        if isinstance(next_resp, Exception):
            raise next_resp
        return next_resp


def test_propose_intent_extracts_tool_call() -> None:
    tool_call = FakeToolCall(
        name="current_route_status",
        arguments='{"entity_mentions": ["front route"]}',
        call_id="call_route_1",
    )
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client, model="llama-3.3-70b-versatile")

    result = provider.propose_intent("Is the front route clear?")

    assert result["intent"] == "current_route_status"
    assert result["entity_mentions"] == ["front route"]
    assert provider.last_tool_call_id == "call_route_1"
    assert provider.last_tool_name == "current_route_status"
    assert len(client.calls) == 1
    assert client.calls[0]["tool_choice"] == "required"


def test_propose_intent_falls_back_to_unsupported_on_missing_tools() -> None:
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(content="Hello there!"))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.propose_intent("What is the meaning of life?")

    assert result["intent"] == "unsupported"
    assert result["entity_mentions"] == []


def test_propose_intent_raises_unavailable_on_client_error() -> None:
    client = FakeGroqClient([Exception("Rate limit reached")])
    provider = GroqIntentProvider(client=client)

    with pytest.raises(IntentProviderUnavailableError) as exc_info:
        provider.propose_intent("Is route A clear?")

    assert "Groq API call failed" in str(exc_info.value)


def test_repair_intent_submits_error_feedback() -> None:
    tool_call = FakeToolCall(
        name="current_route_status",
        arguments='{"entity_mentions": ["route_A"]}',
        call_id="call_repair_1",
    )
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.repair_intent("Is route A clear?", "entity_mentions must not be empty")

    assert result["intent"] == "current_route_status"
    assert result["entity_mentions"] == ["route_A"]
    last_call = client.calls[0]
    assert any("validation error" in str(m.get("content")) for m in last_call["messages"])


def test_reconsider_unsupported_filters_tool_choices() -> None:
    tool_call = FakeToolCall(
        name="current_robot_pose",
        arguments="{}",
        call_id="call_pose_1",
    )
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.reconsider_unsupported(
        "Where are you standing?", ("current_robot_pose", "unsupported")
    )

    assert result["intent"] == "current_robot_pose"
    tool_names = [t["function"]["name"] for t in client.calls[0]["tools"]]
    assert set(tool_names) == {"current_robot_pose", "unsupported"}


def test_generate_grounded_explanation_success() -> None:
    fake_completion = FakeChatCompletion(
        choices=[
            FakeChatChoice(
                message=FakeChatCompletionMessage(
                    content=(
                        "The route was marked clear in the static map, "
                        "but live LiDAR detects an obstacle at 12.0cm."
                    )
                )
            )
        ]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)
    provider.last_tool_call_id = "call_abc"
    provider.last_tool_name = "current_route_status"
    provider.last_tool_args = {"entity_mentions": ["front route"]}

    explanation = provider.generate_grounded_explanation(
        user_question="Is the front route clear?",
        tool_name="current_route_status",
        tool_call_id="call_abc",
        tool_result={"status": "grounded_conflict_resolved", "obstacle_distance_cm": 12.0},
        fallback_explanation="Deterministic fallback",
    )

    assert "LiDAR detects an obstacle at 12.0cm" in explanation
    assert len(client.calls) == 1
    messages = client.calls[0]["messages"]
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "call_abc"
    assert "obstacle_distance_cm" in tool_msg["content"]


def test_generate_grounded_explanation_fallback_on_exception() -> None:
    client = FakeGroqClient([Exception("Connection timeout")])
    provider = GroqIntentProvider(client=client)

    explanation = provider.generate_grounded_explanation(
        user_question="Is the front route clear?",
        tool_name="current_route_status",
        tool_call_id="call_abc",
        tool_result={"status": "grounded_conflict_resolved"},
        fallback_explanation="Safe deterministic fallback.",
    )

    assert explanation == "Safe deterministic fallback."


def test_unconfigured_groq_provider_raises_informative_errors() -> None:
    provider = UnconfiguredGroqProvider(error_message="GROQ_API_KEY is not set.")

    with pytest.raises(IntentProviderUnavailableError, match="GROQ_API_KEY is not set"):
        provider.propose_intent("Is the route clear?")

    with pytest.raises(IntentProviderUnavailableError, match="GROQ_API_KEY is not set"):
        provider.repair_intent("Is the route clear?", "error")

    with pytest.raises(IntentProviderUnavailableError, match="GROQ_API_KEY is not set"):
        provider.reconsider_unsupported("Where are you?", ("current_robot_pose",))


def test_create_provider_offline_mode() -> None:
    config = ProviderConfig(mode="rule-based", api_key=None)
    provider, resolved = create_provider(config)

    assert isinstance(provider, RuleBasedIntentProvider)
    assert resolved.mode == "rule-based"


def test_create_provider_live_mode_with_key() -> None:
    config = ProviderConfig(mode="live", api_key="gsk_test123456789")
    provider, resolved = create_provider(config)

    assert isinstance(provider, GroqIntentProvider)
    assert resolved.mode == "live"
    assert provider.model_name == "llama-3.3-70b-versatile"


def test_create_provider_live_mode_without_key_returns_unconfigured() -> None:
    config = ProviderConfig(mode="live", api_key=None)
    provider, resolved = create_provider(config)

    assert isinstance(provider, UnconfiguredGroqProvider)
    assert resolved.mode == "live"
    with pytest.raises(IntentProviderUnavailableError):
        provider.propose_intent("test")
