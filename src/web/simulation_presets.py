"""Load the named warehouse simulation presets."""

import json
from functools import lru_cache
from pathlib import Path

from src.contracts.simulation import ScenarioDefinition

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/phase_two"
WAREHOUSE_FIXTURE = FIXTURES / "contracts.json"


@lru_cache(maxsize=1)
def warehouse_preset() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        json.loads(WAREHOUSE_FIXTURE.read_text())["valid"]["scenario"]
    )


@lru_cache(maxsize=1)
def warehouse_belief_demo_preset() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate_json(
        (FIXTURES / "warehouse_belief_demo.json").read_text(encoding="utf-8")
    )
