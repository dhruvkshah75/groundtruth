"""Architecture-boundary tests for the procedural package."""

from pathlib import Path

PROCEDURAL_DIRECTORY = Path(__file__).resolve().parents[3] / "src" / "procedural"
FORBIDDEN_IMPORT_TEXT = (
    "import sqlite3",
    "from sqlite3",
    "import networkx",
    "from networkx",
    "src.declarative",
    "src.declaritive",
    "src.sensorimotor",
    "groq",
    "openai",
    "huggingface",
    "streamlit",
    "evidence_resolver",
    "evidenceresolver",
)


def test_procedural_modules_do_not_import_concrete_tier_or_provider_code() -> None:
    for module_path in PROCEDURAL_DIRECTORY.glob("*.py"):
        source = module_path.read_text(encoding="utf-8").lower()

        assert not any(forbidden in source for forbidden in FORBIDDEN_IMPORT_TEXT), module_path
