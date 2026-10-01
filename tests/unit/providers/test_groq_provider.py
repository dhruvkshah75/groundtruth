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
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "multi-perspective question" in system_prompt
    assert "Do not choose 'historical_fact_lookup' alone" in system_prompt


def test_propose_intent_missing_tool_call_is_malformed() -> None:
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(content="Hello there!"))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.propose_intent("What is the meaning of life?")

    assert result.get("error") == "missing_tool_call"
    assert "Hello there!" in result.get("raw_output", "")


def test_propose_intent_multiple_tool_calls_rejected_as_malformed() -> None:
    tool_call_1 = FakeToolCall(
        name="current_route_status",
        arguments="{}",
        call_id="call_1",
    )
    tool_call_2 = FakeToolCall(
        name="current_robot_pose",
        arguments="{}",
        call_id="call_2",
    )
    fake_completion = FakeChatCompletion(
        choices=[
            FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call_1, tool_call_2]))
        ]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.propose_intent("What is the route and where are you?")

    assert result.get("error") == "multiple_tool_calls"
    assert "Expected exactly 1" in result.get("raw_output", "")


def test_propose_intent_invalid_json_arguments_rejected_as_malformed() -> None:
    tool_call = FakeToolCall(
        name="current_route_status",
        arguments="{invalid json",
        call_id="call_bad_json",
    )
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.propose_intent("Is the route clear?")

    assert result.get("error") == "invalid_json_arguments"


def test_propose_intent_unknown_function_rejected_as_malformed() -> None:
    tool_call = FakeToolCall(
        name="execute_arbitrary_code",
        arguments="{}",
        call_id="call_hack",
    )
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.propose_intent("Run code")

    assert result.get("error") == "unknown_function_name"
    assert result.get("raw_name") == "execute_arbitrary_code"


@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    [
        ('{"entity_mentions": "front route"}', "invalid_entity_mentions"),
        ('{"entity_mentions": ["front route", 12]}', "invalid_entity_mentions"),
        ('{"entity_mentions": ["front route"], "intent": "unsupported"}', "unexpected_arguments"),
        ("{}", "missing_required_arguments"),
    ],
)
def test_propose_intent_rejects_arguments_that_do_not_match_tool_schema(
    arguments: str, expected_error: str
) -> None:
    tool_call = FakeToolCall(
        name="current_route_status", arguments=arguments, call_id="call_invalid_args"
    )
    completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )
    provider = GroqIntentProvider(client=FakeGroqClient([completion]))

    result = provider.propose_intent("Is the front route clear?")

    assert result.get("error") == expected_error


def test_propose_intent_explicit_unsupported_tool() -> None:
    tool_call = FakeToolCall(
        name="unsupported",
        arguments="{}",
        call_id="call_unsupported",
    )
    fake_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )
    client = FakeGroqClient([fake_completion])
    provider = GroqIntentProvider(client=client)

    result = provider.propose_intent("What is quantum entanglement?")

    assert result["intent"] == "unsupported"
    assert result["entity_mentions"] == []
    assert result["user_question"] == "What is quantum entanglement?"


def test_propose_intent_raises_unavailable_on_client_error() -> None:
    client = FakeGroqClient([Exception("Rate limit reached")])
    provider = GroqIntentProvider(client=client)

    with pytest.raises(IntentProviderUnavailableError) as exc_info:
        provider.propose_intent("Is route A clear?")

    assert "Groq API call failed" in str(exc_info.value)
    assert provider.last_error == "Rate limit reached"


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
    )

    assert "LiDAR detects an obstacle at 12.0cm" in explanation
    assert len(client.calls) == 1
    messages = client.calls[0]["messages"]
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "call_abc"
    assert "obstacle_distance_cm" in tool_msg["content"]


def test_generate_grounded_explanation_propagates_provider_unavailability() -> None:
    client = FakeGroqClient([Exception("Connection timeout")])
    provider = GroqIntentProvider(client=client)

    with pytest.raises(IntentProviderUnavailableError, match="grounded explanation call failed"):
        provider.generate_grounded_explanation(
            user_question="Is the front route clear?",
            tool_name="current_route_status",
            tool_call_id="call_abc",
            tool_result={"status": "grounded_conflict_resolved"},
        )


def test_generate_grounded_explanation_returns_empty_for_empty_completion() -> None:
    empty_completion = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(content=" "))]
    )
    provider = GroqIntentProvider(client=FakeGroqClient([empty_completion]))

    response = provider.generate_grounded_explanation(
        user_question="Is the front route clear?",
        tool_name="current_route_status",
        tool_call_id="call_abc",
        tool_result={"status": "grounded_conflict_resolved"},
    )

    assert response == ""


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


def test_planner_repairs_malformed_initial_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.contracts import CapabilityDescriptor
    from src.procedural.capability_validator import CapabilityValidator
    from src.procedural.intent_coverage_reviewer import IntentCoverageReviewer
    from src.procedural.intent_planner import IntentPlanner

    # Turn 1: Malformed (plain prose, no tool call)
    turn1_resp = FakeChatCompletion(
        choices=[
            FakeChatChoice(message=FakeChatCompletionMessage(content="I am thinking about it..."))
        ]
    )
    # Turn 2: Repaired tool call
    tool_call = FakeToolCall(
        name="current_route_status",
        arguments='{"entity_mentions": ["front route"]}',
        call_id="call_repaired",
    )
    turn2_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[tool_call]))]
    )

    client = FakeGroqClient([turn1_resp, turn2_resp])
    provider = GroqIntentProvider(client=client)

    caps = [
        CapabilityDescriptor(
            name="lidar_scan",
            kind="sensor",
            description="LiDAR scanner",
        )
    ]
    validator = CapabilityValidator(caps)
    reviewer = IntentCoverageReviewer(validator)
    planner = IntentPlanner(provider, reviewer)

    outcome = planner.plan_intent("Is the front route clear?")

    assert outcome.intent is not None
    assert outcome.intent.intent == "current_route_status"
    assert outcome.intent.entity_mentions == ["front route"]
    assert len(client.calls) == 2


def test_get_provider_config_default_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.providers.config import get_provider_config

    monkeypatch.delenv("GROUNDTRUTH_PROVIDER_MODE", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    config = get_provider_config()
    assert config.mode == "live"
    assert config.has_valid_key is False


def test_get_provider_config_explicit_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.providers.config import get_provider_config

    monkeypatch.setenv("GROUNDTRUTH_PROVIDER_MODE", "offline")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    config = get_provider_config()
    assert config.mode == "rule-based"


def test_get_provider_config_typo_mode_defaults_to_live(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.providers.config import get_provider_config

    # A typo like "ofline" must NOT silently fall back to rule-based
    monkeypatch.setenv("GROUNDTRUTH_PROVIDER_MODE", "ofline")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    config = get_provider_config()
    assert config.mode == "live"
    assert config.has_valid_key is False
