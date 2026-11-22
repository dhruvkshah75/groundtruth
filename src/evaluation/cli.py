"""Command-line interface for replaying evaluation scenarios and exporting logs (P2-12)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Literal

from src.contracts import FactAssertion, FactQuery
from src.declarative.memory_repository import MemoryRepository
from src.evaluation.models import ExpectedQueryEvaluation, ReplayBundle, ScenarioEvaluationLog
from src.evaluation.replay import (
    replay_evidence,
    replay_world,
    run_all_scenarios,
    run_scenario,
    validate_bundle,
)
from src.evaluation.scenarios import ALL_SCENARIO_BUILDERS, scenario_01_map_clear_vs_cart_blocked

LOGGER = logging.getLogger("groundtruth.evaluation.cli")


def map_only_baseline() -> tuple[str, str]:
    """Compare the stored map claim with the recorded first route observation."""
    bundle = scenario_01_map_clear_vs_cart_blocked()
    with MemoryRepository(":memory:") as repo:
        for fact in bundle.metadata["initial_facts"]:
            repo.record_fact(FactAssertion.model_validate(fact))
        facts = repo.query_facts(
            FactQuery(subject="route_A", predicate="status_is", active_only=True)
        )
        if len(facts) != 1:
            raise ValueError("Map-only baseline requires exactly one route status")
        map_status = facts[0].object
    observed_status = bundle.recorded_samples[0].result.measurements["status"]
    return map_status, observed_status


def generate_markdown_report(
    logs: list[ScenarioEvaluationLog],
    mode: str,
    output_path: Path | None = None,
) -> str:
    """Generate a clean, readable Markdown report from evaluation logs."""
    total = len(logs)
    passed = sum(1 for log in logs if log.passed)
    failed = total - passed
    lines = [
        "# GroundTruth Epistemic Evaluation Report (Phase 2)",
        "",
        f"**Execution Mode:** `{mode}`",
        f"**Total Evaluations:** {total} | **Passed:** {passed} | **Failed:** {failed}",
        "",
        "## Summary Table",
        "",
        "| Scenario ID | Mode | Prompt | Expected Status | Actual Status | Result |",
        "| ----------- | ---- | ------ | --------------- | ------------- | ------ |",
    ]

    for log in logs:
        res_str = "PASS" if log.passed else f"FAIL ({log.failure_reason})"
        exp_status = log.expected_outcome.get("status", "N/A")
        act_status = log.actual_outcome.get("status", "N/A")
        lines.append(
            f'| `{log.scenario_id}` | `{log.mode}` | "{log.prompt}" | '
            f"`{exp_status}` | `{act_status}` | **{res_str}** |"
        )

    lines.extend(
        [
            "",
            "## Detailed Scenario Logs",
            "",
        ]
    )

    for i, log in enumerate(logs, start=1):
        lines.extend(
            [
                f"### {i}. {log.scenario_title} (`{log.scenario_id}`)",
                f"- **Mode:** `{log.mode}`",
                f"- **Conflict / Uncertainty Rationale:** {log.conflict_description}",
                f"- **Setup:** {log.setup}",
                f'- **Prompt:** "{log.prompt}"',
                f"- **Expected Outcome:** `{json.dumps(log.expected_outcome)}`",
                f"- **Actual Outcome:** `{json.dumps(log.actual_outcome)}`",
                f"- **Policy Applied:** `{log.policy_applied}`",
                f"- **Input Timeline:** {', '.join(log.input_timeline) or 'None'}",
                f"- **Sample IDs:** {', '.join(map(str, log.recorded_sample_ids)) or 'None'}",
                f"- **Fact IDs:** {', '.join(map(str, log.recorded_fact_ids)) or 'None'}",
                f"- **Revision IDs:** {', '.join(map(str, log.revision_ids)) or 'None'}",
                f"- **Passed:** {'Yes' if log.passed else 'No'}",
            ]
        )
        if log.failure_reason:
            lines.append(f"- **Failure Reason:** {log.failure_reason}")
        lines.append("")

    lines.extend(
        [
            "## Grounding, Attribution & Deliberately Restricted Baseline",
            "",
            "1. **Evidence Grounding:** All beliefs are formed via explicit observation samples",
            "   stored in the Tier 1 SQLite observation ledger. Audience truth is strictly",
            "   segregated and never provided to the agent or evidence replay.",
            "2. **Anti-Interpolation Policy:** The agent strictly refuses to interpolate",
            "   unobserved intermediate coordinates between discrete temporal scans.",
            "3. **Safe Uncertainty:** Sensor outages, broad scans without target sightings, and",
            "   ambiguous entity references return explicit uncertain or ambiguous statuses.",
            "4. **Baseline Scope:** The evaluated scenarios represent a focused 10-case epistemic",
            "   regression suite covering foundational warehouse dynamics.",
            "",
        ]
    )

    if any(log.scenario_id == "scenario_01_map_clear_vs_cart_blocked" for log in logs):
        map_status, observed_status = map_only_baseline()
        lines.extend(
            [
                "## Map-only comparison",
                "",
                f"The initial map reports route_A **{map_status}**; the recorded first "
                f"LiDAR sample reports **{observed_status}**. This compares one route at "
                "one time and is not an aggregate accuracy measurement.",
                "",
            ]
        )

    content = "\n".join(lines)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

    return content


def save_json_summary(
    logs: list[ScenarioEvaluationLog],
    output_path: Path,
) -> None:
    """Save structured JSON results."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "total_evaluations": len(logs),
        "passed": sum(1 for log in logs if log.passed),
        "failed": sum(1 for log in logs if not log.passed),
        "logs": [log.model_dump(mode="json") for log in logs],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="GroundTruth Phase 2 Replay and Evaluation Runner (P2-12)."
    )
    parser.add_argument(
        "--scenario",
        choices=list(ALL_SCENARIO_BUILDERS.keys()),
        default=None,
        help="Run a specific evaluation scenario (default: all 10 scenarios).",
    )
    parser.add_argument(
        "--mode",
        choices=["evidence_only", "world_rerun", "both"],
        default="both",
        help="Replay execution mode (default: both).",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help="Path to an external ReplayBundle JSON file to validate and replay.",
    )
    parser.add_argument(
        "--expectations",
        type=Path,
        default=None,
        help="JSON array of expected query evaluations for an exported run.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to save JSON and Markdown reports.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "both"],
        default="both",
        help="Output report formats to generate (default: both).",
    )

    args = parser.parse_args(argv)
    if args.expectations is not None and args.bundle is None:
        parser.error("--expectations requires --bundle")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    mode: Literal["evidence_only", "world_rerun", "both"] = args.mode
    logs: list[ScenarioEvaluationLog] = []

    if args.bundle is not None:
        LOGGER.info("Loading external bundle from %s", args.bundle)
        try:
            bundle_data = json.loads(args.bundle.read_text(encoding="utf-8"))
            if isinstance(bundle_data, dict) and "observations" in bundle_data:
                bundle = ReplayBundle.from_simulation_export(bundle_data)
            else:
                bundle = ReplayBundle.model_validate(bundle_data)
            if args.expectations is not None:
                expectations = json.loads(args.expectations.read_text(encoding="utf-8"))
                if not isinstance(expectations, list):
                    raise ValueError("Expectations must be a JSON array")
                bundle.expected_evaluations = [
                    ExpectedQueryEvaluation.model_validate(item) for item in expectations
                ]
            validate_bundle(bundle)
            if not bundle.expected_evaluations:
                raise ValueError("Bundle has no expected evaluations")
            if mode in ("evidence_only", "both"):
                logs.extend(replay_evidence(bundle))
            if mode in ("world_rerun", "both"):
                logs.extend(replay_world(bundle))
        except (ValueError, OSError) as exc:
            LOGGER.error("Cannot replay bundle: %s", exc)
            return 1
    elif args.scenario is not None:
        LOGGER.info("Running scenario: %s [mode=%s]", args.scenario, mode)
        logs.extend(run_scenario(args.scenario, mode=mode))
    else:
        LOGGER.info("Running all 10 evaluation scenarios [mode=%s]", mode)
        logs.extend(run_all_scenarios(mode=mode))

    # Output reports
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.format in ("json", "both"):
        json_path = out_dir / "evaluation_summary.json"
        save_json_summary(logs, json_path)
        LOGGER.info("Saved JSON summary to %s", json_path)

    if args.format in ("markdown", "both"):
        md_path = out_dir / "evaluation_report.md"
        generate_markdown_report(logs, mode=mode, output_path=md_path)
        LOGGER.info("Saved Markdown report to %s", md_path)

    total = len(logs)
    passed = sum(1 for log in logs if log.passed)
    failed = total - passed

    print(f"\nEvaluation Results: {passed}/{total} passed ({failed} failed)")
    if failed > 0:
        for log in logs:
            if not log.passed:
                print(f"FAILED: {log.scenario_id} [{log.mode}] - {log.failure_reason}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
