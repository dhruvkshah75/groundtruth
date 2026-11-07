"""Boundary for the replay exporter supplied by P2-12."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.contracts.simulation import ScenarioDefinition, ScenarioEvent
from src.contracts.simulation_api import SimulationExportBundle


@dataclass(frozen=True)
class SimulationExportTarget:
    """Server-side run locator and upper bounds for a coherent export."""

    session_id: str
    run_id: UUID
    generation: int
    epoch_utc: datetime
    sim_time_ms: int
    snapshot_sequence: int
    next_sample_sequence: int
    scenario: ScenarioDefinition
    presenter_events: tuple[ScenarioEvent, ...]


class SimulationExporter(Protocol):
    def export(self, target: SimulationExportTarget) -> SimulationExportBundle:
        """Build the public envelope at the given boundary using run-scoped data."""
        ...
