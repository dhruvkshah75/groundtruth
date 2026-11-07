"""Load the named warehouse from the canonical Phase 2 scenario fixture."""

import json
from functools import lru_cache
from pathlib import Path

from src.contracts.simulation import ScenarioDefinition

WAREHOUSE_FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/phase_two/contracts.json"


@lru_cache(maxsize=1)
def warehouse_preset() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        json.loads(WAREHOUSE_FIXTURE.read_text())["valid"]["scenario"]
    )
