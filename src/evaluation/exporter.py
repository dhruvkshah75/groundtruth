"""Simulation replay exporter implementing the P2-09 SimulationExporter protocol (P2-12)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.contracts.simulation_api import SimulationExportBundle
from src.web.simulation_export import SimulationExportTarget

LOGGER = logging.getLogger("groundtruth.evaluation.exporter")


class DefaultSimulationExporter:
    """Production replay exporter implementing SimulationExporter protocol (P2-12)."""

    def __init__(self, session_lookup: Callable[[str], Any]) -> None:
        self._session_lookup = session_lookup

    def export(self, target: SimulationExportTarget) -> SimulationExportBundle:
        """Export run-scoped evidence into a validated SimulationExportBundle."""
        session = self._session_lookup(target.session_id)
        with session.lock:
            # Query all observation samples for this run up to the snapshot sim_time_ms
            observations = session.agent.memory.query_samples(
                run_id=target.run_id,
                end_ms=target.sim_time_ms,
            )
            # Retrieve belief changes recorded by the simulation runtime
            changes = list(session.runtime.belief_changes)

        LOGGER.info(
            "Exported simulation run %s: %d samples, %d belief changes at sim_time_ms=%d",
            target.run_id,
            len(observations),
            len(changes),
            target.sim_time_ms,
        )

        return SimulationExportBundle(
            schema_version=1,
            run_id=target.run_id,
            epoch_utc=target.epoch_utc,
            sim_time_ms=target.sim_time_ms,
            snapshot_sequence=target.snapshot_sequence,
            scenario=target.scenario,
            presenter_events=list(target.presenter_events),
            observations=observations,
            belief_changes=changes,
        )
