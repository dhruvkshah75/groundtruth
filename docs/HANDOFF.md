# I, Agent: Project Handoff

## Purpose

This repository implements **"I, Agent: A Three-Layer Epistemic Architecture for Grounded Agents."**

The project demonstrates an agent that does not merely generate plausible answers. It must maintain evidence-backed beliefs, reconcile historical memory with live observations, preserve provenance, distinguish perspectives, and explicitly refuse claims it cannot verify.

The authoritative assignment description is in `I_Agent_AI_Project_Proposal.pdf`. Supporting design material is in `docs/`.

## Problem Statement

Conventional LLMs can hallucinate, lack semantic grounding, and struggle to track viewpoint-dependent claims. This project addresses those weaknesses with three isolated layers:

1. **Tier 1: Declarative layer**
   A NetworkX belief graph with SQLite-backed provenance and fact history.
2. **Tier 2: Procedural layer**
   A tool-calling LLM/ReAct-style orchestrator. This is the only layer allowed to coordinate Tier 1 and Tier 3.
3. **Tier 3: Sensorimotor layer**
   A changing mock environment that provides sensor observations.

The layers must remain decoupled: Tier 1 must not import or call Tier 3, and Tier 3 must not import or call Tier 1.

## Evaluation-Critical Scenarios

### Scenario A: Historical map versus live obstacle evidence

A static map says a route is clear. A recent LiDAR observation says the route is blocked at 12 cm.

Expected behavior:

- The agent selects the recent, direct observation for its current operational belief.
- The static map claim remains available as historical evidence; it is not deleted or overwritten.
- The answer explains the conflict and cites the evidence/source/time that justified the decision.

### Scenario B: Perspective separation

The user requests a red box. The camera currently sees the box as brown under yellow lighting. Historical maintenance data calls it blue.

Expected behavior:

- User request, current perception, and maintenance history are stored as separate claims.
- The agent answers according to the requested perspective and does not collapse these claims into one invented truth.
- The answer can explain what each source/perspective reports.

## Current Repository State

The repository is an early Tier 1 scaffold, not a runnable three-layer agent.

- `src/declaritive/db_manager.py` creates a SQLite `memory_log` table and can read all rows.
- `src/main.py` and `tests/test_scenarios.py` are empty.
- `pyproject.toml` includes Python 3.11+, NetworkX, Pydantic, pytest, and Ruff.
- There is no belief graph implementation, fact write/query API, revision logic, Tier 2, Tier 3, LLM adapter, composed demo, or automated epistemic scenarios yet.

Do not assume documented modules already exist. Some development docs reference paths such as `tests/integration/test_groundedness.py` and `src/observability/dashboard.py`, but they are not currently present.

## Important Documentation

- `I_Agent_AI_Project_Proposal.pdf`: assignment goals, expected architecture, scenarios, and deliverables.
- `README.md`: repository orientation.
- `docs/I_Agent_Masterplan.md`: initial phased plan and database expectations.
- `docs/Database_Schema_Guide.md`: Tier 1 persistence rationale and schema direction.
- `docs/tier1/Contracts_Architecture_Guide.md`: proposed cross-layer Pydantic models, boundaries, and audit/revision requirements.
- `docs/tier1/Grounded_Query_Guardrails.md`: anti-hallucination rules and query behavior.
- `docs/Local_Development_Setup.md`: local setup and quality checks.

The contracts guide labels itself as a proposed team decision. Review and settle it before treating it as final.

## Recommended Tier 1 Design

### Design Principle

Build a deterministic, auditable **belief ledger plus query graph**, not a general-purpose knowledge-graph platform. Tier 1 decides what evidence supports a claim. Tier 2 may convert that structured result into natural language but must not invent or silently choose evidence.

### Source of Truth

- SQLite is the durable, authoritative record for assertions, provenance, revision links, and audit events.
- NetworkX is an in-memory projection used for relationship traversal and explanation paths.
- Rebuild the graph from SQLite on startup, or synchronize it after each write.

### Fact Model

At minimum, each fact/assertion should contain:

- Stable fact ID (UUID recommended)
- Subject, predicate, object/value
- Source agent/system and source type
- Confidence score
- Observation time and record/ingestion time
- Context or perspective
- Evidence reference/payload
- Status or validity information
- Revision/supersession link and reason when applicable

Use generic facts at the Tier 1 boundary. Do not encode `LiDAR`, routes, cameras, or colour-specific logic into Tier 1.

### History and Revision Rules

- Facts are append-only. Never delete or mutate a historical assertion to make it fit the current state.
- A later assertion that changes the operational conclusion creates a new fact linked to the prior fact.
- Record why a fact was superseded, for example `newer_direct_observation`, `higher_confidence_source`, or `context_changed`.
- A superseded fact is historical evidence, not necessarily a falsehood. It may still be correct for a previous time or perspective.

### Deterministic Belief Selection

For a current-belief query, use transparent and testable ordering:

1. Match the requested context/perspective first.
2. Prefer direct observations over inferred or static knowledge for dynamic state questions.
3. Prefer newer relevant observations.
4. Use source reliability and confidence as tie-breakers.
5. Do not merge incompatible claims into a fabricated answer.

The result should include the selected fact, relevant alternatives/conflicts, applied decision rule, and evidence identifiers.

### Unsupported Questions

If no evidence supports an answer, return a structured inability-to-verify result. The LLM must not be allowed to fabricate evidence, sources, tool results, or facts.

## Decisions To Resolve Before Significant Implementation

1. Choose one persistence table name. Existing material conflicts between `memory_log` and `provenance_log`.
2. Confirm the shared Pydantic contract and its exact required fields.
3. Decide whether Tier 1 fact IDs are UUID strings or another stable identifier.
4. Define the canonical timestamp format and timezone policy.
5. Define a small source-type taxonomy and initial trust/reliability values.
6. Decide whether `superseded_by` is stored directly on a historical record, via a separate revision table, or both.
7. Resolve the package spelling: the code currently uses `src/declaritive/`, while all design documentation calls the layer `declarative`.

## Suggested Implementation Order

1. Finalize contracts and layer-boundary rules.
2. Complete SQLite schema, migrations/initialization, indexes, validation, and append-only write methods.
3. Add audit and revision records with referential integrity.
4. Implement graph projection and graph/database consistency checks.
5. Implement current, historical, source-scoped, context-scoped, and time-bounded fact queries.
6. Implement deterministic conflict resolution and structured explanations.
7. Write Tier 1 tests before integrating an LLM.
8. Add the mock sensor environment behind Tier 3 contracts.
9. Add a narrow Tier 2 orchestrator that translates tool/sensor output into facts and renders evidence-backed responses.
10. Build a deterministic terminal or lightweight visual demo for both required scenarios.

## Minimum Automated Test Suite

The assignment calls for ten automated epistemic test logs. A strong initial set is:

1. Persist and retrieve a fact with complete provenance.
2. Reject malformed facts, invalid confidence values, and invalid timestamps.
3. Store separate claims from different sources without overwriting either.
4. Retrieve historical claims after a revision.
5. Prefer a recent direct observation over an older static claim.
6. Preserve and expose the superseded predecessor with its revision reason.
7. Return conflicting evidence when the query requests it.
8. Isolate claims by context/perspective.
9. Return inability-to-verify for unsupported questions.
10. Verify graph/SQLite consistency after fact ingestion and revision.

Also add an architectural boundary test confirming Tier 1 does not import Tier 2 or Tier 3. All core tests must run without an API key or live LLM; LLM responses should be mocked.

## Evaluation Strategy

The strongest differentiator is epistemic transparency rather than a larger LLM feature set.

- Show an evidence timeline: map claim, live observation, belief revision, and current decision.
- Show a source/perspective comparison view for competing assertions.
- Explain "why not the map?" using the exact freshness, directness, confidence, and source rules.
- Demonstrate deterministic replay: the same inputs produce the same belief state and explanation.
- Demonstrate uncertainty and refusal when evidence is insufficient.
- Keep the resolution policy inspectable in logs or the UI instead of hiding decisions in prompts.

## Future Expansion Ideas

- Learn source reliability from agreement with verified observations.
- Add evidence freshness decay for dynamic facts without deleting history.
- Support temporal intervals and questions such as "what changed since 10:02?"
- Add sensor-specific uncertainty models beyond a single confidence score.
- Support multi-agent provenance, trust boundaries, and disagreement analysis.
- Add counterfactual explanations such as "what evidence would change this decision?"
- Add a graph/timeline dashboard for demonstrations and debugging.
- Use vector retrieval only to map language to known entities; never use it as the source of truth.
- Connect real/simulated robotics middleware while retaining the same Tier 3 contracts.
- Add safety policies requiring corroboration for high-impact actions.

## Quality Gate

Before handoff or evaluation, a fresh clone should pass:

```bash
uv sync --all-groups
uv run pytest
uv run ruff format --check .
uv run ruff check .
```

The final demo should be fully reproducible without a live LLM. An optional LLM integration may improve interaction quality, but it must not be required to prove the core epistemic behavior.
