"""Tests for the narrow, non-executing intent-provider boundary."""

from src.procedural import IntentProvider


class FakeIntentProvider:
    """Small stand-in for later IntentPlanner tests.

    Fields:
        calls: The provider calls received during a test.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def propose_intent(self, user_question: str) -> object:
        self.calls.append(("propose", user_question))
        return {"intent": "current_route_status"}

    def repair_intent(self, user_question: str, validation_error: str) -> object:
        self.calls.append(("repair", user_question, validation_error))
        return {"intent": "unsupported"}

    def reconsider_unsupported(
        self, user_question: str, candidate_intents: tuple[str, ...]
    ) -> object:
        self.calls.append(("reconsider", user_question, candidate_intents))
        return {"intent": "unsupported"}


def test_fake_provider_satisfies_the_intent_provider_boundary() -> None:
    provider = FakeIntentProvider()

    assert isinstance(provider, IntentProvider)


def test_provider_boundary_keeps_raw_outputs_and_records_no_tool_calls() -> None:
    provider = FakeIntentProvider()

    first = provider.propose_intent("Can I move forward?")
    repaired = provider.repair_intent("Can I move forward?", "invalid intent")
    reconsidered = provider.reconsider_unsupported(
        "Is anything blocking me?", ("current_route_status", "unsupported")
    )

    assert first == {"intent": "current_route_status"}
    assert repaired == {"intent": "unsupported"}
    assert reconsidered == {"intent": "unsupported"}
    assert provider.calls == [
        ("propose", "Can I move forward?"),
        ("repair", "Can I move forward?", "invalid intent"),
        (
            "reconsider",
            "Is anything blocking me?",
            ("current_route_status", "unsupported"),
        ),
    ]
