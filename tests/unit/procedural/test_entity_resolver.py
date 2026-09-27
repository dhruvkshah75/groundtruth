"""Tests for the narrow entity-resolution boundary."""

from src.contracts import EntityResolution
from src.procedural import EntityResolver


class FakeEntityResolver:
    """Test resolver that returns pre-arranged entity results.

    Fields:
        results: Entity results keyed by the input mention.
        mentions: Entity mentions received during a test.
    """

    def __init__(self, results: dict[str, EntityResolution]) -> None:
        """Store entity results for later lookup.

        Args:
            results: Entity results keyed by user-facing mentions.
        """
        self.results = results
        self.mentions: list[str] = []

    def resolve_entity(self, mention: str) -> EntityResolution:
        """Return the result configured for one mention.

        Args:
            mention: The user-facing entity mention to resolve.

        Returns:
            The configured entity result.
        """
        self.mentions.append(mention)
        return self.results[mention]


def test_fake_entity_resolver_satisfies_the_boundary() -> None:
    resolver = FakeEntityResolver(
        {
            "front route": EntityResolution(
                mention="front route",
                status="resolved",
                canonical_entity_id="route_ahead",
            )
        }
    )

    assert isinstance(resolver, EntityResolver)
    assert resolver.resolve_entity("front route").canonical_entity_id == "route_ahead"
