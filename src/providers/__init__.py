"""Model provider integration package for GroundTruth.

Exports configuration, Groq LLM function-calling provider, tools, and provider factory.
"""

from __future__ import annotations

from typing import Any

from src.procedural.intent_provider import IntentProvider, IntentProviderUnavailableError
from src.providers.config import ProviderConfig, get_provider_config
from src.providers.groq_provider import GroqClientInterface, GroqIntentProvider, RealGroqClient
from src.providers.tools import ALLOWED_INTENTS, INTENT_TOOLS


class UnconfiguredGroqProvider:
    """Placeholder provider used when live mode is selected without a valid API key."""

    def __init__(self, error_message: str = "GROQ_API_KEY is not configured.") -> None:
        self._error_message = error_message
        self.model_name = "llama-3.3-70b-versatile (unconfigured)"
        self.last_tool_name = "unconfigured"
        self.last_tool_call_id = "none"
        self.last_tool_args: dict[str, Any] = {}

    def propose_intent(self, user_question: str) -> object:
        raise IntentProviderUnavailableError(self._error_message)

    def repair_intent(self, user_question: str, validation_error: str) -> object:
        raise IntentProviderUnavailableError(self._error_message)

    def reconsider_unsupported(
        self, user_question: str, candidate_intents: tuple[str, ...]
    ) -> object:
        raise IntentProviderUnavailableError(self._error_message)


def create_provider(
    config: ProviderConfig | None = None,
) -> tuple[IntentProvider, ProviderConfig]:
    """Instantiate the appropriate IntentProvider based on configuration.

    - Live mode with key: GroqIntentProvider backed by RealGroqClient.
    - Live mode without key: UnconfiguredGroqProvider that raises clear errors.
    - Offline mode: RuleBasedIntentProvider.
    """
    resolved_config = config or get_provider_config()

    if resolved_config.mode == "live":
        if resolved_config.has_valid_key:
            assert resolved_config.api_key is not None
            client = RealGroqClient(api_key=resolved_config.api_key)
            provider = GroqIntentProvider(client=client, model=resolved_config.model)
            return provider, resolved_config
        else:
            provider = UnconfiguredGroqProvider(
                error_message=(
                    "GROQ_API_KEY is not configured. Add GROQ_API_KEY to your .env file "
                    "or set GROUNDTRUTH_PROVIDER_MODE=offline to use offline rule-based mode."
                )
            )
            return provider, resolved_config

    from src.procedural.rule_based_provider import RuleBasedIntentProvider

    return RuleBasedIntentProvider(), resolved_config


__all__ = [
    "ALLOWED_INTENTS",
    "INTENT_TOOLS",
    "GroqClientInterface",
    "GroqIntentProvider",
    "ProviderConfig",
    "RealGroqClient",
    "UnconfiguredGroqProvider",
    "create_provider",
    "get_provider_config",
]
