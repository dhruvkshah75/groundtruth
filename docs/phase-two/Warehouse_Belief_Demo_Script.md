# Warehouse belief demo recording runbook

This three-minute walkthrough follows Parcel 01 and the patrol in the same run of
`warehouse_belief_demo_v1`. The audience sees the whole simulated warehouse. The
robot uses what its camera and LiDAR actually recorded, the static map, and
stored evidence. Show both views when the parcel moves behind the shelf, then
show how new observations change the robot's conclusions before you ask a question.

## Prepare a clean take

From the repository root, build the frontend and start the Python server in
explicit offline mode. Use Node.js 20.19 or newer for the build.

```bash
uv sync --locked
(cd frontend && npm ci && npm run build)
GROUNDTRUTH_PROVIDER_MODE=offline uv run --locked python -m src.web.server
```

Open [the simulation workspace](http://127.0.0.1:8765/simulation) in a fresh
browser profile or a new chat. Use a desktop viewport of at least 1440 × 900
and keep the Simulation tab foregrounded. Check [API health](http://127.0.0.1:8765/api/health)
before recording. It should report `Offline (deterministic rules)`. Say or
caption **“Simulated warehouse · Offline deterministic answers”**. If a take
uses Groq, verify live mode with API health and label that take accordingly.

On desktop, open the left sidebar for the preset and playback speed. On a
narrow screen or with the sidebar closed, expand **Presenter controls** below
the simulation clock. Choose **Warehouse belief demo** and click **Load preset**.
Confirm **Paused**, **0.000 s**, and **0.5×**. Click Parcel 01 on the map if it
isn't selected, then open **Belief and evidence → Location evidence**.

Use **Play** between beats. Click **Pause** about 0.5–1.0 simulation seconds
before each target, then click **Step** until the clock matches the target.
Every Step advances 0.100 simulation seconds. At 0.5×, reaching 33.000 s takes
about 66 seconds of playback, leaving time for inspection and narration.
The preset schedules the parcel and cart changes automatically.

## Three-minute shot list

| Time in video | Pause at | What to show and say |
| --- | --- | --- |
| 0:00–0:30 | **0.000 s** | Show the loaded preset, clock, map and belief panel. Point to Parcel 01 at Station A (240, 240 cm), **Verified · Station A**, and the camera capture at 0.000 s. “This map shows the whole simulation. The robot starts with a camera sighting at Station A.” |
| 0:30–0:52 | **6.500 s** | Show the parcel at (365, 355 cm) behind the shelf on **Simulated world**. In **Belief and evidence**, show **Current location unknown**, **Last seen · Station A · 6.000 s**, and the latest camera attempt at 6.500 s. “We can see where the parcel went. The robot only knows where it last saw it.” |
| 0:52–1:15 | **12.500 s** | Show **Verified · (390, 240) cm** with its fresh camera capture and **Earlier station · Station A**. “The camera sees the parcel again in the aisle. These coordinates are outside either station.” |
| 1:15–1:45 | **19.500 s** | Show **Verified · Station B** at (500, 240 cm). Open **Observation and revision timeline** and find **Belief revision** at 19.500 s. Show the earlier station A belief, the updated station B belief, and **Raw record → Source sample**. “The new sighting supports Station B, and the earlier evidence remains in the ledger.” Collapse the timeline when done. |
| 1:45–2:00 | **24.000 s** | Establish the baseline. Show the patrol path on the map, **Patrol · Moving**, and **Front LiDAR → No obstacle in range**. For the route record, select **Route A** in the **Entity** menu and show **Stored belief · clear**. Return the selection to Parcel 01. |
| 2:00–2:17 | **28.500 s** | Show the cart in the route and **Patrol · Blocked**. In **Patrol evidence**, open **Prior stop → Open prior stop LiDAR sample**. Point to the original decision and capture at **28.000 s** and the **50 cm** stopping threshold. The route was first reported blocked at 25.500 s. The controller withheld motion when the obstacle reached its threshold. |
| 2:17–2:31 | **33.000 s** | Show the cart clear of the route and the patrol moving again. The current decision uses clearance from **32.500 s**. The prior stop still opens the same **28.000 s** LiDAR sample. Confirm that **Station B** has a fresh **33.000 s** camera capture. Leave the clock paused here. |
| 2:31–2:57 | **33.000 s** | Submit the four prompts below in **Ask about the run**. Hold the current location answer next to **Location evidence** long enough to read the matching capture time and sample ID. Use the revision and prior-stop records for the two explanations. |
| 2:57–3:00 | **33.000 s** | End on the workspace or answer. “The robot updated its beliefs as evidence arrived, and every answer can point back to that evidence.” |

Use the side-by-side map and belief panels for the first three beats so the
viewer can compare them without following a tab change. For a graph close-up,
use **Focus earlier belief** or **Focus updated belief** on the Station B
timeline revision, then open **Belief graph**. Station A is the superseded
station fact and Station B is the active one at that checkpoint. Return to
Simulation before pressing Play. The revision row alone is enough if time is tight.

## Ask these four questions at 33.000 s

Paste each exact wording into **Your question** and click **Ask**. All four
answers are captured at 33.000 s. The source observations have different times.

| Exact question | Evidence to compare on screen |
| --- | --- |
| `Where is parcel_01 now?` | Station B at (500, 240 cm). Compare the camera sample ID in the answer with **Location evidence → Current sighting** at 33.000 s. |
| `Where was parcel_01 at 6.5 seconds?` | Station A coordinates (240, 240 cm) from the **6.000 s** camera sighting, at or before the cutoff. The **6.500 s** miss supplies no intermediate location. Show the last-sighting ID captured during the 6.500 s beat. |
| `What evidence caused parcel_01's location belief to change?` | The first Station B detection at **19.500 s** replaced the Station A belief under `camera_observation_revision`. Compare the ID with **Raw record → Source sample** on the revision row. The evidence establishes the two sightings and leaves the unseen transit unspecified. |
| `Why did the robot stop?` | The controller withheld motion at **28.000 s** with an obstacle **50 cm** away. Compare the ID with **Open prior stop LiDAR sample**. |

Sample IDs and the run ID are generated afresh on every take. Match them
within the same take. Use a short zoom on a matching ID instead of reading
UUIDs aloud. At the start, allow one sentence for the architecture. Tier 3
acquires the simulated readings, Tier 1 stores the SQLite observations and
revisions, and Tier 2 uses those records to assess and answer. The belief
graph projects the stored relationships.

## Reset and recover

- **Start over.** Pause and use **Reload preset** to return to 0.000 s at 0.5×,
  or **Reset run** to preserve the current rate. Both start a fresh run with
  its own samples and the same scripted changes. If you overshoot a checkpoint,
  reload and approach it again. Use Step for the final few tenths of a second.
- **Recover the connection.** If the workspace marks a snapshot stale, restart
  the backend if needed, confirm API health, refresh the workspace and reload
  the demo in the selected chat. If live answers are slow or unavailable,
  restart in offline mode and record a fresh, clearly labelled offline take.
- **Rehearse an outage separately.** Use **Warehouse inspection** in a separate
  chat. Under **Presenter disruptions**, select **Disable camera** or
  **Disable LiDAR**, click **Schedule disruption**, and step or play to its
  receipt time. Compare camera-unavailable evidence or the patrol waiting for
  LiDAR with the normal sequence. Enable the sensor or reset afterward.

## Implementation and verification

- `POST /api/simulation/load` accepts `preset: "warehouse_belief_demo_v1"`,
  a new `request_id`, and the current `expected` run ID and generation from
  `GET /api/simulation/state`. The response starts paused at zero with a fresh
  identity. The original `warehouse_inspection_v1` remains available. The
  generated frontend contract includes both presets. See the
  [simulation API reference](Simulation_API.md) for headers and control requests.
- The [manual step integration test](../../tests/integration/test_warehouse_belief_demo.py)
  checks the engine, patrol, run-scoped samples, graph annotations, revision
  and all four answers at the same checkpoints. The
  [browser rehearsal](../../frontend/scripts/check-simulation-browser.mjs)
  times the real Play/Pause/Step path and checks a second run by manual stepping.
  It checks that the opened evidence matches the answer citations and rejects
  prior-run samples after reset.
- The [ten-scenario evaluation report](../../artifacts/evaluation/evaluation_report.md)
  is the WD-05 evidence handoff. Use this runbook and the WD-06 PR rehearsal
  results for the [WD-07 final submission and recording tracker](https://github.com/dhruvkshah75/groundtruth/issues/38).
