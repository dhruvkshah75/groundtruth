# Phase Two: 30-Second Simulation Demo and UI

**Status: proposed presentation and product design, not implemented.** The purpose is to make the professor's changing-environment question visible and understandable in a short demonstration. The underlying behavior and boundaries are specified in [Dynamic Simulation and Time-Aware Beliefs](Dynamic_Simulation_Plan.md).

## Presentation objective

Show an independently changing 2D world, what the robot actually observes, how its belief graph and history change, and how the agent answers a question about that history. The visual motion should come from the same running simulation and observations used by the backend, not a prerecorded animation masquerading as live agent behavior.

The professor should be able to understand the story without reading raw JSON or following a long technical trace. Keep detailed evidence and audit data accessible for questions after the demo.

## Proposed screen

Use the existing React application as the starting point. One coordinated workspace can contain:

1. **World view:** a clear 2D room/grid with robot, table, red cup and obstacle. Animate positions from backend simulation state. Show a clock and the camera field of view. Label this as the audience's world view, not the agent's knowledge.
2. **Robot observation:** show the most recent camera reading, its timestamp, and whether the cup is visible. Clearly mark stale or unavailable observations.
3. **Belief and timeline:** highlight the current or last-seen belief, prior observation, time and source. Show a compact event timeline; update the existing belief graph from backend facts, emphasizing the changed relationship rather than recreating the graph visually without data.
4. **Agent answer:** a readable response to a selected or typed question, with supporting evidence available on expansion. Use the existing response synthesis where appropriate, without implying the LLM itself observed the environment.
5. **Controls:** play, pause, reset, step and a small set of scenario presets. Display simulation time, sensing status and provider mode. A presenter can pause to answer questions.

Visual states should make the distinction between **world truth**, **sensor observation**, and **agent belief** unmistakable. When the cup is hidden from the robot but visible in the audience view, label the agent's location as last seen or unknown. Do not let frontend world data bypass the sensor boundary into the agent.

## Suggested 30-second sequence

Timing is a target for the visual sequence, not a guarantee of LLM response latency. Preload the scenario; allow a brief question and explanation immediately afterwards if necessary.

| Time | What the audience sees | What the system should show |
| --- | --- | --- |
| 0–5 s | Robot sees the red cup at table A. | Camera observation and time; belief graph shows the supported location. |
| 5–12 s | Cup moves to table B within view. | Next scan reports the new location; timeline and active belief update while the prior sighting remains in history. |
| 12–18 s | Ask “Where was it before, and what changed?” | Answer identifies both sightings and their times, without inventing the path or cause. |
| 18–24 s | Cup moves out of view or behind an obstacle. | Audience can see the world, while sensor panel says not visible and belief is marked last seen. |
| 24–30 s | Ask “Where is it now?” | Answer gives the last sighting and says the current location is unverified. |

If response latency prevents two live questions in 30 seconds, run the visual sequence first and ask the questions immediately afterward. Use a visible preset question or have it ready to submit; do not prefill a fabricated response. Optionally bring the cup back into view to show reacquisition during the longer discussion.

## Visual and interaction requirements

- Use restrained motion and clear labels, colors and icons so the professor can follow the event without a narration-heavy explanation. Include text labels rather than relying on color alone.
- Synchronize displayed world, sensor time and belief updates with the backend; distinguish delayed observations from the present world state.
- Make graph changes legible: focus the relevant object and relationship, animate or highlight a genuine revision, and show the superseded relationship in history rather than silently erasing it.
- Explain a missing detection without implying disappearance. A blind spot should make the knowledge limitation visible.
- Keep raw audit payloads and technical identifiers behind an expandable detail view. The primary answer should explain source, time, previous belief and reason for change in ordinary language.
- Maintain the existing chat and original scenarios. Responsive layouts and keyboard-operable controls should keep the core demo usable on the presentation screen.
- Handle loading, paused state, disconnected backend, sensor failure and unavailable LLM explicitly; never substitute a canned answer for an error.

## Implementation order

1. Render the actual 2D world and clock; connect deterministic play, pause, reset and step to backend simulation state.
2. Display sensor visibility and observations independently from the audience world view.
3. Connect observation processing, temporal memory and graph updates; add a compact timeline.
4. Connect temporal questions and readable answers to the same recorded evidence.
5. Rehearse the timed sequence, measure latency, refine visual hierarchy and test on the presentation machine.

## Demo acceptance and backup

The demo is ready when a clean reset consistently reproduces the visible-move and out-of-view cases, the graph corresponds to actual backend beliefs, and the answer distinguishes current observation from last seen. Verify pause, step, reset, provider status and failure messages. Run automated scenario tests and prepare a concise evidence log for each displayed decision.

Use a recorded demonstration as a clearly labelled backup for network or hardware failure, and keep deterministic offline mode available with its limitations disclosed. Rehearse both. Do not claim an offline rule-based answer came from the live LLM.

## How to explain its contribution

The presentation can say: “The simulated world changes independently. Our robot samples its sensors, keeps a time-stamped history, revises supported beliefs, and tells us when its last observation is no longer enough to know the current state.” Show the actual limitations: simplified 2D sensing, simulated object identity and no physical robot unless integration has been completed.

If delivered and evaluated, a possible resume description is: “Built a 2D dynamic-world simulation and a time-aware robot belief system that tracks sensor observations, records belief revisions, and explains uncertainty from partial visibility.” Adjust it to match the work actually completed and measured.
