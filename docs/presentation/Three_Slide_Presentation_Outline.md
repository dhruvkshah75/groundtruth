# GroundTruth: Three-Slide Presentation Outline

Use this as a 3-slide structure for the team presentation. It is deliberately concise; use the [Project Study Guide](Project_Study_Guide.md) for presenter notes, examples and likely questions.

## Slide 1 — The problem and the agent flow

**Title:** GroundTruth: an evidence-grounded three-tier agent

**On the slide:**

- **Problem:** language models can confuse an old claim, a live observation and a user's perspective.
- **Tier 1 — Declarative:** SQLite evidence/history + NetworkX active belief graph.
- **Tier 2 — Procedural:** LLM intent proposal + deterministic validation, evidence plan and policy.
- **Tier 3 — Sensorimotor:** deterministic mock robot world and sensor observations.
- **Flow:** question → allowed function call → validated plan → memory/sensor evidence → deterministic evaluation → guarded explanation.

**Speaker line:** “The model helps understand the request, but Python decides what evidence to gather and what the evidence supports.”

**Team contributions to mention:**

- **Dhruv Shah:** shared cross-tier contracts and Tier 2 intent planning; architecture and integration work.
- **Aayush Kushwaha:** Tier 1 SQLite evidence ledger and live Groq/ReAct provider integration.
- **Rajat:** Tier 3 mock environment and plan-execution/composition connection.
- **Aadi-hash:** NetworkX active-belief graph and consistency checks.

## Slide 2 — Scenario A: map claim versus live sensor

**Title:** Groundedness: a map can be stale

**On the slide:**

```text
SQLite map claim: route_A is clear
Mock LiDAR: obstacle is blocked, 12 cm ahead
Policy: lidar_overrides_map
Outcome: store a sensor-backed successor + audit event; refresh active graph
```

- Robot is at `(0, 0)` in `room_101`, facing north, frame `robot_base`.
- The forward obstacle is at `(0, 12)` cm; the sensor computes the reading from the world state.
- The older map assertion stays in history; it is marked superseded in the active belief lifecycle.

**Speaker line:** “The agent checks memory and LiDAR. It does not let an old static map overrule the relevant current observation.”

**Show:** Load Scenario A, ask “Is the front route clear?”, expand the ReAct trace and inspector audit/telemetry.

## Slide 3 — Scenario B, contribution recap and next work

**Title:** Keep perspectives separate; keep extending evaluation

**On the slide:**

| Perspective | Scenario B result |
|---|---|
| User expectation | Red |
| Current camera under yellow light | Brown |
| Historical maintenance claim | Blue, sourced from `bot_02` |

- These claims have different sources and meanings; “blue” is a historical record, not proof of current appearance.
- Assignment Milestones 1–4 are implemented: contracts/storage/mock environment; LLM ReAct; Scenario A; Scenario B.
- Next: broaden scenario/wording tests, capture repeatable grading logs, and only then expand mock actions or persistence.

**Speaker line:** “The project has completed the assignment's listed milestones on the current branch. Our next work is stronger evaluation and deliberate extensions, not a missing milestone from the PDF.”

**Show:** Load Scenario B, ask the suggested three-perspective question, point to the three cards, camera telemetry and historical source.

## Presenter handoff

- **Dhruv:** explain why contracts and Tier 2 prevent arbitrary model behavior, then walk through the flow.
- **Rajat:** explain how the mock robot and sensors calculate observations from position, direction and world state.
- **Aadi-hash:** explain why SQLite is authoritative and why the graph is a rebuildable active view.
- **Aayush:** explain the evidence ledger/evaluator and the live LLM function-call → tool result → guarded response cycle.

These speaking roles are suggestions based on the issue/PR ownership in the repository. The team can change who presents each section without changing implementation credit.

