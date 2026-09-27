"""Validation for the capabilities advertised by Tier 3."""

from collections.abc import Sequence
from typing import Literal

from src.contracts import CapabilityDescriptor

CapabilityKind = Literal["sensor", "action"]


class CapabilityConfigurationError(ValueError):
    """Raised when the advertised capability registry is invalid."""


class UnknownCapabilityError(ValueError):
    """Raised when a plan requests a capability that was not advertised."""


class CapabilityKindMismatchError(ValueError):
    """Raised when a capability has a different kind than the plan requires."""


class CapabilityValidator:
    """Check requested capabilities against a fixed advertised registry.

    Args:
        capabilities: The sensor and action descriptors advertised by Tier 3.

    The input is copied during setup. Later changes to the caller's list or
    descriptors cannot change this validator's registry.
    """

    def __init__(self, capabilities: Sequence[CapabilityDescriptor]) -> None:
        """Create a validator from unique capability descriptors.

        Args:
            capabilities: The advertised capability descriptors.

        Raises:
            CapabilityConfigurationError: If two descriptors share a name.
        """
        registry: dict[str, CapabilityDescriptor] = {}
        for descriptor in capabilities:
            if descriptor.name in registry:
                raise CapabilityConfigurationError(f"duplicate capability name: {descriptor.name}")
            registry[descriptor.name] = descriptor.model_copy(deep=True)
        self._registry = registry

    def require_capability(self, name: str, kind: CapabilityKind) -> CapabilityDescriptor:
        """Return a copied descriptor when its name and kind both match.

        Args:
            name: The exact advertised capability name requested by a plan.
            kind: The required operation kind, either ``sensor`` or ``action``.

        Returns:
            A copy of the matching capability descriptor.

        Raises:
            UnknownCapabilityError: If no capability has the requested name.
            CapabilityKindMismatchError: If its kind does not match.
        """
        descriptor = self._registry.get(name)
        if descriptor is None:
            raise UnknownCapabilityError(f"unknown capability: {name}")
        if descriptor.kind != kind:
            raise CapabilityKindMismatchError(
                f"capability {name} has kind {descriptor.kind}, expected {kind}"
            )
        return descriptor.model_copy(deep=True)

    def has_capability(self, name: str, kind: CapabilityKind) -> bool:
        """Check whether an exact capability name has the requested kind.

        Args:
            name: The exact capability name to check.
            kind: The expected operation kind.

        Returns:
            ``True`` only when the registry contains that exact name and kind.
        """
        descriptor = self._registry.get(name)
        return descriptor is not None and descriptor.kind == kind

    def advertised_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        """Return copied descriptors from the fixed registry.

        Returns:
            A tuple of capability descriptors in their original input order.
        """
        return tuple(descriptor.model_copy(deep=True) for descriptor in self._registry.values())
