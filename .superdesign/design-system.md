# GroundTruth Agent UI — design direction

## Product and implementation

GroundTruth is a three-layer epistemic agent demo. The UI is a React 18 + TypeScript single-page app built with Vite. A small Python standard-library HTTP server connects the browser to the existing Python agent. The current intent provider is deterministic and rule based; it is not a live commercial LLM.

The interface lets a student ask questions and inspect the agent's answer, evidence, belief revisions, active graph, SQLite history, and mock sensorimotor world. Scenario A and B controls seed the actual backend environment. All displayed state comes from API responses; connection errors must remain visible instead of being replaced with demo data.

## Visual source

Use the user's attached Colibri screenshot as the visual reference: compact dark charcoal chat interface, near-black narrow left rail, centered conversation, soft gray message surface, restrained white/gray text, subtle borders, rounded composer, and modest teal accent. Keep the project identity textual as “GroundTruth” / “I, Agent”; there is no logo asset. Do not invent a logo or use emojis. Small functional line icons are acceptable.

## Interaction and content requirements

- Keep chat and question entry central. Expose system details in a labeled inspector without obscuring scenario results.
- Preserve Scenario A and B controls and reset. Explain that each preset changes the mock environment and seeds its facts.
- Show user expectation, live sensor perspective, and historical perspective separately when evidence exists, with source and evidence details.
- Show Tier 1 graph and ledger, Tier 2 plan/result/revision, and Tier 3 robot, lighting, obstacles, objects, and sensor telemetry from the backend.
- Distinguish absent data from a known value. Never show sample/fallback facts as if the agent observed them.
- Report API connectivity and deterministic provider status honestly.
- Use accessible labels, keyboard-operable controls, responsive layout, and clear loading/error states. Do not use emoji characters.

## Color and type

Use charcoal and near-black surfaces, off-white primary text, muted gray secondary text, thin low-contrast separators, and one restrained teal accent for actions. Use a clean sans-serif font and compact readable type. Avoid gradients, decorative illustrations, and excessive shadows. Current token values and component dimensions are recorded in `.superdesign/init/theme.md`.
