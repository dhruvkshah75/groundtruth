<p align="center">
  <img src="docs/assets/logo.png" alt="GroundTruth logo" width="600">
</p>

# GroundTruth: Three-Layer Epistemic Agent

GroundTruth is a student project about answering questions using evidence from stored claims and a robot's current simulated environment. Its central idea is simple: an old map, a live sensor, and a user's expectation may disagree, so the system should preserve their sources and context instead of treating every claim as the same kind of truth.

## Current project status

The current project is a working local demo with a React/TypeScript interface connected to a Python API and agent. The backend runs a bounded ReAct cycle (using Groq function calling in live mode, or deterministic rule-based matching in explicit offline mode), retrieves facts from Tier 1, obtains observations from the Tier 3 mock environment, applies deterministic evidence and conflict rules, validates response consistency, and returns the result for the UI to display.

The demo includes two end-to-end scenarios:

- **Scenario A — map and LiDAR conflict:** stored map memory says the front route is clear, while the simulated LiDAR detects an obstacle 12 cm ahead. The agent records a belief revision and shows its evidence and audit details.
- **Scenario B — distinct color perspectives:** the user expects a red object, the simulated camera can perceive it as brown under yellow lighting, and Tier 1 history can contain a third-party record that it was painted blue. The agent reports these as separate perspectives.

![GroundTruth running Scenario A](docs/assets/agent_chat_graph_demo.png)
*Scenario A showing the agent comparing stored map knowledge with a live LiDAR observation with the*
*interactive belief graph showing active facts and relationships.*

![GroundTruth running Scenario B](docs/assets/current_agent_demo1.png)
*Scenario B demonstrating different color perspectives.*

![GroundTruth simulation](docs/assets/simulation.png)
*Questions being asked parallely with the onging simulation of the robot in a warehouse.*

**LLM/ReAct status:** The app implements a bounded LLM function-calling ReAct loop via `GroqIntentProvider` (milestone [GT-07 / issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17)), while retaining an explicit offline mode with `RuleBasedIntentProvider`. Python owns fact validation, approved operations, and belief revisions. In live mode the model can explain the verified result in natural language; a separate model review checks non-verbatim answers against the evidence, falling back to the deterministic explanation when review fails. This model review reduces errors but cannot guarantee correctness. Execution traces are expandable in chat, and evidence and telemetry remain available in the System inspector.

The browser demo supports up to five chats, each with its own messages, SQLite fact ledger, and mock-world state. The server saves those sessions locally in `data/chat-sessions.sqlite.sessions/`, while the chat list is kept in the browser's local storage. Titles are derived from the first question, with the scenario name used until then. Deleting a chat frees a slot; resetting the Agent clears only the selected chat. The frontend displays backend results and API errors; it does not replace missing backend data with canned scenario answers.

## Three layers

1. **Tier 1 — Declarative memory:** SQLite stores sourced facts, timestamps, confidence, revisions, and audit history. NetworkX is a rebuildable projection of active facts, not another source of truth.
2. **Tier 2 — Procedural layer:** validates model intent proposals, resolves entities, builds and executes mandatory evidence plans, and applies conflict/perspective rules. In live mode, a Groq adapter proposes intent and generates an explanation; non-verbatim answers receive a separate model review before display.
3. **Tier 3 — Sensorimotor layer:** the deterministic mock environment calculates LiDAR, camera, ambient-light, and robot-pose observations from its configured world.

The Python layer owns the evidence and answer logic. The browser displays data returned by that backend; it does not substitute canned answers when requests fail. Scenario presets seed repeatable backend test worlds.

## Run the app

You need `make`, `uv`, Python 3, Node.js 20.19+, and npm. From the project root, install the dependencies:

```bash
make install
```

Start the Python API and React development server together:

```bash
make dev
```

The two servers run in parallel in the same terminal. Open <http://127.0.0.1:5173> and press **Ctrl+C** to stop both. If either server exits, the other is stopped as well.

To run them separately, use these commands in two terminals:

```bash
make api
```

```bash
make frontend
```

Run `make help` to list the available commands. The API does not automatically reload after Python changes; restart `make dev` or `make api` to pick them up.

Restart the Python API process after updating to this version. A previous version's in-memory sessions are lost on restart; sessions saved by this version persist across subsequent restarts.

Open <http://127.0.0.1:5173>. See [Local Development Setup](docs/Local_Development_Setup.md) for production build instructions and verification commands.

## Documentation

- [Current Agent and Scenario Guide](docs/Implemented_System_Overview.md) — what currently runs, key terms, complete Scenario A/B walkthroughs, and limitations.
- [Documentation index](docs/README.md) — architecture, tier guides, and project context.
- [Architecture decisions](docs/Architecture_Decisions.md) — the approved tier boundaries and evidence policy.
- [GT-07 / Issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17) — real LLM ReAct integration and frontend connection.
