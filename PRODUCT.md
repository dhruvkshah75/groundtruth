# GroundTruth

GroundTruth is a working local student demo of a three-layer epistemic agent. It answers questions using stored claims and a robot's simulated environment, preserving sources and context when memory, observations, and user expectations disagree.

## Confirmed workspace scope

- Up to five saved chats, without a logged-in user, account controls, or avatars. Chat titles come from the first question, or the loaded scenario until a question is asked.
- Left navigation for **Chat**, **System inspector**, and **Belief graph**. The inspector is a dedicated page rather than a persistent right sidebar beside the chat.
- Simple **Load Scenario A** and **Load Scenario B** controls on the left, without explanatory descriptions; retain reset and backend/provider connection feedback.
- A sidebar toggle completely hides navigation and releases its width to the workspace. On compact screens, navigation opens as a dismissible drawer.
- A readable chat with formatted natural-language answers, status, perspectives, revisions, and expandable execution traces. Detailed evidence remains available in the System inspector.
- An interactive Three.js belief graph with usable nodes and relationships, selection details, search/filter controls, camera controls, and an accessible fact-list alternative. Node names and readable relationship predicates are restored, with collision handling prioritizing focused labels, node names, then changed and ordinary relationship labels. Labels that cannot fit may hide; facts remain available in details and the fact list.

## Existing behavior to preserve

The React/TypeScript frontend displays the Python API's results and errors. Python owns evidence validation, approved operations, deterministic conflict rules, and belief revisions. In live mode a separate model review checks generated explanations against the evidence, with deterministic fallback when review fails; this probabilistic check cannot guarantee correctness. SQLite is the fact ledger; NetworkX is a rebuildable projection rather than a separate source of truth. The bounded ReAct cycle uses the configured Groq provider in live mode and explicit rule-based matching in offline mode. The interface must not substitute canned answers when backend requests fail.

Scenario A compares map memory with a simulated LiDAR obstacle and records a belief revision. Scenario B separates user expectation, camera perception under lighting, and historical color records. Scenario presets seed repeatable backend worlds. Each chat has its own Agent state, saved locally by the server; the chat list is stored in the browser. Sensors are simulated.

## Visual reference and constraints

The supplied [Figma reference](https://www.figma.com/design/i9pLwoqVaWRwZYNWz7yAne/caseaimatch-design--Community-?node-id=2-672), specifically **Home - Response** (`2:2675`), informs the white/lilac composition, left navigation, and reading column. It is a visual reference, not the source or a request for its complete feature set. Do not import case-management, account, or avatar features from it. Retain the existing GroundTruth name and BrainCircuit/Lucide icon language; use `Agent` for the visible assistant name. The graph shares the app’s white/lilac theme and readable sans-serif typography, with purple nodes, amber revisions, and gray superseded relationships. This interface refresh introduces no production raster assets.

Operate mode prioritizes the current conversation and inspectable evidence. Preserve the existing backend, graph data, API contracts, and evidence behavior while adjusting presentation. See `DESIGN.md` for the current implementation's visual system and `README.md` for backend architecture and local setup.
