"""The narrow entity-resolution boundary used by Tier 2 planning."""

from typing import Protocol, runtime_checkable

from src.contracts import EntityResolution


@runtime_checkable
class EntityResolver(Protocol):
    """Resolve one user-facing name without exposing a Tier 1 repository."""

    def resolve_entity(self, mention: str) -> EntityResolution:
        """Resolve one entity mention.

        Args:
            mention: The user-facing name, such as ``front route``.

        Returns:
            A resolved, ambiguous, or missing entity result.
        """
