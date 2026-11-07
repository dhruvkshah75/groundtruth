# Simulation workspace (P2-10)

[Issue #34](https://github.com/dhruvkshah75/groundtruth/issues/34) ·
[Implementation PR #46](https://github.com/dhruvkshah75/groundtruth/pull/46)

The Simulation tab opens `/simulation` in the existing GroundTruth app.
The active chat's session owns the warehouse run. The same route works on a
direct visit or refresh through the SPA fallback from P2-09.

## Prerequisites

- [P2-01 / #21](https://github.com/dhruvkshah75/groundtruth/issues/21), merged
  as `27afc24` in [PR #26](https://github.com/dhruvkshah75/groundtruth/pull/26),
  supplies the [warehouse contracts](Shared_Simulation_Contracts.md).
- [P2-09 / #33](https://github.com/dhruvkshah75/groundtruth/issues/33), merged
  as `619e420` in [PR #45](https://github.com/dhruvkshah75/groundtruth/pull/45),
  supplies the [HTTP API](Simulation_API.md), including the durable restore
  generation and paused-state response revisions. Its runtime and patrol come
  from [P2-07](Session_Runtime.md) and [P2-06](Patrol_Controller.md).

## Run the workspace

From the repository root, build the frontend and serve it with the backend.

```bash
(cd frontend && npm ci && npm run build)
GROUNDTRUTH_PROVIDER_MODE=offline uv run --locked python -m src.web.server
```

Open [the local simulation route](http://127.0.0.1:8765/simulation). Select
**Load preset**. The warehouse starts paused at zero. Press **Step** to advance
100 simulation milliseconds or **Play** to watch the robot at the default 0.5×.
A 30-second run takes about one wall-clock minute at that rate, or 15 seconds
at 2×. **Reload preset**
starts a fresh run and restores the default rate. On desktop, the open left
sidebar contains the preset, playback speed and disruption controls. With the sidebar closed
or on a narrow screen, open **Presenter controls** above the map to reach
the same actions. The **Evidence** link jumps to the panels below the map.

When the workspace has at least 900 px available beside the sidebar, drag
a navigation tab to the left or right drop area. Shift+Left Arrow places it
on the left, and Shift+Enter places it on the right. Drag the center divider
or use its arrow keys to adjust the widths. The right pane can be closed or
opened full screen. The sidebar shows saved chats whenever Chat is visible
and graph controls whenever Belief graph is visible. Clicking a different tab
replaces the left pane and keeps the right pane open. Switching saved chats
keeps the split and loads the selected session in both panes. The layout
returns to one pane as the window narrows.

Open **Presenter disruptions** to schedule cart movement, parcel relocation to
a named station, lighting changes, or camera and LiDAR outages and recovery. A
confirmed receipt shows the scheduled simulation time. A paused run must step
or play to reach it. The runtime checks for conflicts with scripted and queued
events, so a repeated or overlapping disruption can return a validation error.

## Rendering and evidence

- **Audience world** renders the latest backend geometry as an accessible
  React/SVG map. Three.js supplies vector math for the field of view and
  position interpolation. The room uses centimeters in `warehouse_map`, east
  to the right and north up. SVG y is room height minus map y. The camera fan
  marks geometric heading and range. The [sensor implementation](Warehouse_Sensors.md)
  determines actual visibility, occlusion and acquisition. The expandable map
  list gives object coordinates and the robot's authoritative pose.
- **Robot observation** reads the backend's latest camera and front LiDAR
  attempts. Each has its original simulation capture time. Camera rows show
  detected objects and their apparent color. An empty capture, an unavailable
  sensor, and an old sample have separate labels. A LiDAR obstacle measurement
  and the controller's patrol state are shown separately.
- **Agent belief** displays bounded fact and revision references supplied by
  the runtime. [P2-11 / #35](https://github.com/dhruvkshah75/groundtruth/issues/35)
  owns the richer timeline, graph and answer coupling.

The inspector's Tier 3 section displays the warehouse's audience-only physical
state, including its objects and solid obstacles. The scenario supplies entity
names to the resolver, but their positions are not sent to the agent as observation
evidence. The active belief graph and entity resolver are scoped to the current
run; prior mock and simulation evidence remains stored.

The [workspace](../../frontend/src/SimulationWorkspace.tsx) calls the shared
[client](../../frontend/src/api.ts) through [useSimulation](../../frontend/src/useSimulation.ts).
The hook binds requests to the active session ID, orders snapshots by
generation and revision, and checks run IDs within a generation. It polls
with a 250 ms wall-clock cadence when visible, at most one state request at
a time, with capped backoff after errors. Unmounting or switching sessions
cancels timers and requests. Returning to a visible tab fetches a fresh state
and respects the backend's current playback status.

The [map](../../frontend/src/WarehouseMap.tsx) interpolates between received
positions only while running with a live connection and motion enabled.
It uses the last authoritative positions when paused, disconnected, reset
or reduced motion is enabled. Interpolation is visual only. Evidence times
always use simulation milliseconds. A stale response cannot replace a newer
run, and a failed request keeps the last data marked stale. A mutation stays
pending until the backend replies. Errors from a stale run or a scheduling
conflict are visible, and the client does not automatically replay mutations.

## Verification

- `uv run --locked pytest -q` passed all 650 backend tests in a clean copy
  without local provider credentials.
- `uv run --locked ruff check .` and `uv run --locked ruff format --check .`
  passed.
- `npm ci`, `npm run build`, `npm run test:graph` and `npm run test:display`
  passed in `frontend`.
- The [Playwright check](../../frontend/scripts/check-simulation-browser.mjs)
  passed against the production build and a real offline backend at 1440,
  1024, 768 and 390 pixels. It covers the heading and coordinates, real motion,
  keyboard focus and step, paused rate changes, all disruption types, conflict
  and stale-run errors, outages and recovery, delayed polls, visibility,
  navigation, per-chat state, two-sided docking, divider resizing, scrollbars,
  composer growth, themed dropdowns, and presenter controls on a narrow
  screen. It also checks text contrast and reduced motion for controls.
  The screenshots come from loaded server state.

To repeat the browser check, run the build and server above, then from
`frontend` install Chromium once and run the check.

```bash
npx playwright install chromium
npm run test:simulation
```

Set `CHROME_PATH` to use an existing Chrome executable,
`GROUNDTRUTH_TEST_URL` to select a server, or
`GROUNDTRUTH_SIM_SCREENSHOTS` to write screenshots to a directory.
The test uses two fresh browser session IDs. It holds or aborts real requests
to exercise response ordering and disconnect behavior. Generated fixtures are
available for contract tests. The workspace and browser verification do not
render fixture snapshots as live data.

[Desktop, light theme](images/simulation-workspace-desktop.png) ·
[Simulation with docked Chat](images/simulation-workspace-split.png) ·
[Narrow viewport, dark theme](images/simulation-workspace-mobile.png)
