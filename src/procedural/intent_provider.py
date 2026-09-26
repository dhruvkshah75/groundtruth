"""The narrow interface between intent planning and an LLM-backed provider.

The provider returns untrusted JSON-like data only. ``IntentPlanner`` will
validate that data against the shared ``IntentRequest`` contract before it can
influence any later planning step.
"""

from typing import Protocol, runtime_checkable


class IntentProviderUnavailableError(Exception):
    """Raised when a provider cannot complete an expected request."""


@runtime_checkable
class IntentProvider(Protocol):
    """Propose or correct a constrained intent without executing any tools.

    Implementations may use a local fake in tests or a structured-output LLM
    adapter later. Every method returns ``object`` because the result is raw,
    untrusted data until ``IntentPlanner`` validates it.
    """

    def propose_intent(self, user_question: str) -> object:
        """Propose an intent.

        Args:
            user_question: The original question from the user.

        Returns:
            Raw JSON-like data that should describe one ``IntentRequest``.
        """

    def repair_intent(self, user_question: str, validation_error: str) -> object:
        """Correct one invalid proposal.

        Args:
            user_question: The original question from the user.
            validation_error: A short description of what was invalid.

        Returns:
            Raw JSON-like data for one corrected ``IntentRequest``.
        """

    def reconsider_unsupported(
        self, user_question: str, candidate_intents: tuple[str, ...]
    ) -> object:
        """Reconsider an unsupported proposal within supplied choices.

        Args:
            user_question: The original question from the user.
            candidate_intents: Allowed reconsideration choices, including
                ``unsupported``.

        Returns:
            Raw JSON-like data for one reconsidered ``IntentRequest``.
        """
