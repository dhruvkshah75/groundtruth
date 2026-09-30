"""Architecture boundary tests for GT-05 graph modules.

Verifies that active_graph.py and consistency.py:
- import no Tier 2 modules
- import no Tier 3 modules
- do not import or call an LLM provider
- do not apply source-priority or conflict-resolution logic
- never use NetworkX for persistent storage (no nx.write_*/nx.read_* calls)
"""

from __future__ import annotations

import ast
import inspect
import types
from pathlib import Path

import src.declarative.active_graph as active_graph_mod
import src.declarative.consistency as consistency_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GRAPH_MODULES = (active_graph_mod, consistency_mod)
_SRC_ROOT = Path(__file__).parents[4] / "src" / "declarative"


def _direct_module_imports(mod: types.ModuleType) -> set[str]:
    """Return the set of top-level module names imported inside *mod*."""
    names: set[str] = set()
    for attr in vars(mod).values():
        if isinstance(attr, types.ModuleType) and attr.__name__:
            names.add(attr.__name__)
    return names


def _source_text(mod: types.ModuleType) -> str:
    """Return the source code of *mod* as a string."""
    return inspect.getsource(mod)


def _ast_names(mod: types.ModuleType) -> set[str]:
    """Return all Name and Attribute node ids in the module's AST."""
    tree = ast.parse(_source_text(mod))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


# ---------------------------------------------------------------------------
# Tier isolation tests
# ---------------------------------------------------------------------------


def test_no_tier2_imports() -> None:
    """active_graph and consistency must not import any src.procedural module."""
    for mod in _GRAPH_MODULES:
        imported = _direct_module_imports(mod)
        bad = [n for n in imported if n.startswith("src.procedural")]
        assert not bad, f"{mod.__name__} must not import Tier 2 (src.procedural); found: {bad}"


def test_no_tier3_imports() -> None:
    """active_graph and consistency must not import any Tier 3 module."""
    tier3_prefixes = ("src.sensorimotor", "src.environment", "src.tier3")
    for mod in _GRAPH_MODULES:
        imported = _direct_module_imports(mod)
        bad = [n for n in imported if any(n.startswith(p) for p in tier3_prefixes)]
        assert not bad, f"{mod.__name__} must not import Tier 3 modules; found: {bad}"


def test_no_llm_provider_import_or_call() -> None:
    """active_graph and consistency must not import or call an LLM provider."""
    llm_markers = (
        "openai",
        "anthropic",
        "google.generativeai",
        "genai",
        "litellm",
        "langchain",
        "llama",
        "cohere",
        "mistral",
        "transformers",
        "huggingface",
    )
    for mod in _GRAPH_MODULES:
        src = _source_text(mod).lower()
        for marker in llm_markers:
            assert marker not in src, f"{mod.__name__} must not reference LLM provider '{marker}'"


def test_no_source_priority_or_conflict_resolution_logic() -> None:
    """active_graph and consistency must not contain source-priority or
    conflict-resolution identifiers.

    This is a narrow, targeted check: we look for identifier names that
    would only appear if confidence decay, source ranking, or winner-selection
    logic was implemented.  A code-review comment is the ultimate gate for
    logic absence; this test guards against accidental re-introduction.
    """
    forbidden_identifiers = (
        "decay",
        "rank",
        "winner",
        "trust",
        "priority",
        "resolve_conflict",
        "select_winner",
        "choose_belief",
        "confidence_decay",
        "source_rank",
    )
    for mod in _GRAPH_MODULES:
        names = _ast_names(mod)
        bad = [ident for ident in forbidden_identifiers if ident in names]
        assert not bad, (
            f"{mod.__name__} must not contain conflict-resolution identifiers; found: {bad}"
        )


def test_networkx_never_used_for_persistent_storage() -> None:
    """active_graph and consistency must never call nx.write_* or nx.read_* functions."""
    nx_persistence_calls = (
        "write_graphml",
        "write_gexf",
        "write_gml",
        "write_gpickle",
        "write_graph6",
        "write_sparse6",
        "write_edgelist",
        "write_multiline_adjlist",
        "write_adjlist",
        "write_pajek",
        "write_shp",
        "write_yaml",
        "read_graphml",
        "read_gexf",
        "read_gml",
        "read_gpickle",
        "read_graph6",
        "read_sparse6",
        "read_edgelist",
        "read_multiline_adjlist",
        "read_adjlist",
        "read_pajek",
        "read_shp",
        "read_yaml",
    )
    for mod in _GRAPH_MODULES:
        src = _source_text(mod)
        for call in nx_persistence_calls:
            assert call not in src, (
                f"{mod.__name__} must not use NetworkX persistence function '{call}'"
            )
