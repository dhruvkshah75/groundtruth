"""Tests for entity alias resolution in MemoryRepository."""

from src.contracts import EntityResolution
from src.declarative.memory_repository import MemoryRepository

# ---------------------------------------------------------------------------
# basic resolution outcomes
# ---------------------------------------------------------------------------


def test_resolve_entity_resolved_single_mapping() -> None:
    with MemoryRepository() as repo:
        repo.add_alias("route A", "entity:route_A")
        result = repo.resolve_entity("route A")
        assert result.status == "resolved"
        assert result.canonical_entity_id == "entity:route_A"


def test_resolve_entity_ambiguous_two_mappings() -> None:
    with MemoryRepository() as repo:
        repo.add_alias("the box", "entity:box_01")
        repo.add_alias("the box", "entity:box_02")
        result = repo.resolve_entity("the box")
        assert result.status == "ambiguous"
        assert len(result.candidates) == 2
        assert "entity:box_01" in result.candidates
        assert "entity:box_02" in result.candidates


def test_resolve_entity_missing_unknown_alias() -> None:
    with MemoryRepository() as repo:
        result = repo.resolve_entity("nonexistent_thing")
        assert result.status == "missing"
        assert result.canonical_entity_id is None
        assert result.candidates == []


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


def test_resolve_entity_normalization_strips_whitespace() -> None:
    """Leading/trailing whitespace must not affect resolution."""
    with MemoryRepository() as repo:
        repo.add_alias("front route", "entity:front_route")
        result = repo.resolve_entity("  Front Route  ")
        assert result.status == "resolved"
        assert result.canonical_entity_id == "entity:front_route"


def test_resolve_entity_normalization_casefolding() -> None:
    """Upper-case variant must resolve to same mapping."""
    with MemoryRepository() as repo:
        repo.add_alias("ROUTE A", "entity:route_A")
        result = repo.resolve_entity("route a")
        assert result.status == "resolved"


def test_resolve_entity_normalization_collapse_whitespace() -> None:
    """Multiple internal spaces must be collapsed to single space."""
    with MemoryRepository() as repo:
        repo.add_alias("front  route", "entity:front_route")
        result = repo.resolve_entity("front route")
        assert result.status == "resolved"
        assert result.canonical_entity_id == "entity:front_route"


# ---------------------------------------------------------------------------
# determinism and deduplication
# ---------------------------------------------------------------------------


def test_resolve_entity_candidates_alphabetical_order() -> None:
    """Ambiguous candidates must be returned in deterministic alphabetical order."""
    with MemoryRepository() as repo:
        repo.add_alias("block", "entity:z_block")
        repo.add_alias("block", "entity:a_block")
        result = repo.resolve_entity("block")
        assert result.status == "ambiguous"
        assert result.candidates == ["entity:a_block", "entity:z_block"]


def test_resolve_entity_duplicate_mapping_does_not_multiply() -> None:
    """Adding the same alias→canonical pair twice must still resolve as 'resolved'."""
    with MemoryRepository() as repo:
        repo.add_alias("route A", "entity:route_A")
        repo.add_alias("route A", "entity:route_A")  # duplicate
        result = repo.resolve_entity("route A")
        assert result.status == "resolved"
        assert result.canonical_entity_id == "entity:route_A"


def test_resolve_entity_returns_entity_resolution_instance() -> None:
    with MemoryRepository() as repo:
        result = repo.resolve_entity("anything")
        assert isinstance(result, EntityResolution)
