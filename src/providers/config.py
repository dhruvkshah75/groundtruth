"""Configuration and environment management for GroundTruth model providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def load_env_file(env_path: Path | None = None) -> None:
    """Load simple KEY=VALUE pairs from a .env file into os.environ if not already set."""
    path = env_path or (Path(__file__).resolve().parents[2] / ".env")
    if not path.is_file():
        return

    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
                continue
            key, val = trimmed.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        # Ignore .env read errors silently
        pass


@dataclass(frozen=True)
class ProviderConfig:
    """Runtime configuration for intent and explanation providers."""

    mode: Literal["live", "rule-based"]
    api_key: str | None
    model: str = DEFAULT_GROQ_MODEL
    timeout_seconds: float = 30.0

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def has_valid_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


def get_provider_config() -> ProviderConfig:
    """Resolve provider configuration from environment and .env."""
    load_env_file()

    mode_env = os.environ.get("GROUNDTRUTH_PROVIDER_MODE", "").strip().lower()
    api_key = os.environ.get("GROQ_API_KEY", "").strip() or None
    model = os.environ.get("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL

    # If explicitly requested offline/rule-based
    if mode_env in ("offline", "rule-based", "rule_based", "rules"):
        mode: Literal["live", "rule-based"] = "rule-based"
    elif mode_env in ("live", "groq", "llm"):
        mode = "live"
    else:
        # Default policy: live mode if key is present; otherwise rule-based
        mode = "live" if api_key else "rule-based"

    return ProviderConfig(
        mode=mode,
        api_key=api_key,
        model=model,
    )
