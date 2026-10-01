# GroundTruth: Three-Layer Epistemic Agent

GroundTruth is a student project about answering questions using evidence from stored claims and a robot's current simulated environment. Its central idea is simple: an old map, a live sensor, and a user's expectation may disagree, so the system should preserve their sources and context instead of treating every claim as the same kind of truth.

## Three layers

1. **Tier 1 — Declarative memory:** SQLite stores sourced facts, timestamps, confidence, revisions, and audit history. NetworkX is a rebuildable projection of active facts, not another source of truth.
2. **Tier 2 — Procedural layer:** deterministic current code validates an intent, resolves entities, builds and executes a safe evidence plan, and applies the conflict/perspective rules. A real LLM provider is planned, not connected yet.
3. **Tier 3 — Sensorimotor layer:** the deterministic mock environment calculates LiDAR, camera, ambient-light, and robot-pose observations from its configured world.

The Python layer owns the evidence and answer logic. The browser displays data returned by that backend; it does not substitute canned answers when requests fail. Scenario presets seed repeatable backend test worlds.

## Run the app

Install Python dependencies with `uv sync --all-groups`. You also need Node.js 20.19+ and npm.

Start the Python API in one terminal:

```bash
uv run python -m src.web.server
```

Start the React development server in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. See [Local Development Setup](docs/Local_Development_Setup.md) for production build instructions and verification commands.

## Documentation

- [Current Agent and Scenario Guide](docs/Implemented_System_Overview.md) — what currently runs, key terms, complete Scenario A/B walkthroughs, and limitations.
- [Documentation index](docs/README.md) — architecture, tier guides, and project context.
- [Architecture decisions](docs/Architecture_Decisions.md) — the approved tier boundaries and evidence policy.
- [GT-07 / Issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17) — planned real LLM ReAct integration and frontend update.

