# P2-02 deterministic warehouse engine

`src.sensorimotor.simulation_engine.SimulationEngine` implements the headless Tier 3
physical core. It uses P2-01 contracts without modifying legacy MockEnvironment,
replace_world, restore_world or the existing A/B presets. It does not start a loop,
read a wall clock, produce observations, write beliefs or run a patrol.

## Headless example

```python
import json
from datetime import UTC, datetime
from pathlib import Path
from src.contracts import ScenarioDefinition
from src.sensorimotor.simulation_engine import SimulationEngine

examples = json.loads(Path("tests/fixtures/phase_two/contracts.json").read_text())
engine = SimulationEngine(
    ScenarioDefinition.model_validate(examples["valid"]["scenario"]),
    epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
)
assert engine.snapshot().status.sim_time_ms == 0
engine.play()
for _ in range(60):
    engine.advance()
engine.pause()
print(engine.snapshot().entities[0].footprint)  # parcel midpoint: (115,370)
checkpoint = json.loads(json.dumps(engine.export_state()))
restored = SimulationEngine.restore(checkpoint)
assert restored.snapshot() == engine.snapshot()
restored.step()  # paused manual advance by 100 simulation ms
```

## Public operations and lifecycle

- Constructor/load: validated detached scenario, explicit timezone-aware epoch,
  injectable UUID factory; new paused run at zero. Invalid scenes leave a loaded
  engine unchanged. Initial solid/solid and robot/solid overlap is rejected;
  non-solid station/parcel markers may intentionally overlap.
- play/pause: idempotent; completed state remains completed. No physical version change.
- advance: one tick only when running, otherwise no-op. step: one tick only while
  paused; running/completed manual step is an error. Default 300 ticks finish at
  exactly 30,000 ms with no automatic loop.
- set_rate: validated 0.25/0.5/1, initially 0.5; no time jump or physics change.
  Reset retains selected rate; loading a fresh default scenario returns to 0.5.
- reset: initial scene, fresh run ID, same epoch/config, zero elapsed time/event
  progress/input log; no sensor acquisition is performed here.
- snapshot: detached P2-01 audience state; cannot be used by Tier 2 as sensor evidence.
- queue_event: validated future MoveEvent/LightingEvent/SensorAvailabilityEvent;
  stable (at_ms,sequence), same validation as scenario scripts, no duplicate keys or
  overlapping motion for the same entity. Preserves physical-input submission order.
  Only linear movement is currently accepted; this does not expose an unrestricted
  teleport/relocate API. Presenter HTTP controls belong to P2-09.
- command_robot: one next-tick cardinal displacement bounded by speed × tick length,
  optional cardinal turn; identity/room/frame and footprint bounds must match.
  Only one pending command is accepted. This is an internal P2-06 integration primitive,
  not an advertised tool, autonomous patrol or safety decision. It is consumed once,
  including when physical collision prevents its execution.

## Time, motion and event ordering

All advancement is explicit, in simulation milliseconds. P2-07 will schedule calls
using its monotonic wall-clock accumulator: 100 simulation ms nominally takes 200
wall ms at 0.5×. This core never sleeps. Camera acquisition at 500 simulation ms
and freshness calculations belong downstream and are unaffected by playback rate.

On a tick, advance the candidate time, bring previously active segments to that
boundary, apply due scripted/queued inputs ordered by (at_ms,sequence), integrate
new motion, and apply an allowed pending robot command. Completing an existing
segment before capturing a new segment's origin makes adjacent moves continuous.
At a normal event start its movement fraction is zero; subsequent ticks interpolate
from its captured origin and absolute elapsed fraction, never accumulated deltas.
Events at zero first execute on the first advancing tick, with the corresponding
elapsed fraction; merely reading t=0 never executes a script. This is an explicit
boundary refinement of the original high-level ordering, not a sensor callback.

world_version increments once per tick whose final physical/sensor state differs,
not once per moved object. Time-only ticks leave it unchanged. snapshot_sequence
counts executed ticks (controls do not increment it). Reads and controls do not
change physical versions. Invalid inputs are validated before commit, and ticks
build a candidate state before publishing it.

## Collision semantics

The merged fixture defines a circular robot and rectangular cart/shelf footprints.
The engine uses swept AABB tests for solid rectangles; robot circle contacts are
conservatively tested against radius-expanded rectangles. Tangential contact is
permitted, interior intersection is blocked. This may stop a circle before an exact
rounded-corner contact, deliberately favoring no penetration over geometric fidelity.

If a movement would cross solid geometry, reject that object's whole tick movement
rather than tunnel or slide. Motion continues to use its absolute scripted fraction;
it may catch up if a later swept path becomes clear, but cannot cross the blocker.
Solid motions also reserve their swept bounding boxes for subsequent movers/robot
commands in that tick. This prevents crossing trajectories from passing through
one another, conservatively rejecting some non-simultaneous traversals. Iteration
order is deterministic scenario entity/motion insertion order; no random physics.
Parcel markers are explicitly non-solid. There are no rigid-body forces or pushing.

This is physical collision prevention, not proof that a sensor detected an obstacle.
LiDAR-driven stop/resume decisions and patrol planning remain P2-06 responsibilities.

## Save/restore and concurrency handoff

export_state returns a JSON-safe version-1 checkpoint containing full initial
scenario/config, current snapshot and the ordered submitted physical input log
(including a pending robot command). Snapshot time and script/input history encode
motion progress and event cursor. restore reconstructs that progress by deterministic
replay, then verifies the reconstructed snapshot against the saved one. Corrupted
physical snapshots, unknown inputs, unsupported versions and unordered/future
submission logs fail. Payloads are deep copied, not trusted live model references.

Replay restoration costs O(elapsed ticks + events), intentionally bounded by the
short demo rather than introducing a second opaque mutable motion-state format.
Selected rate and lifecycle are restored exactly; P2-07 can explicitly pause after
restore. UUID factory can be injected for later resets. No SQLite/session persistence
or scheduler integration is performed. Callers must serialize access to an engine;
there are no internal threads or claims of concurrent mutation safety.

P2-03 can read a detached snapshot to calculate sensor measurements at its capture
time. The application must keep this audience/physics interface out of the
agent-visible SensorSource. No import of Tier 1, Tier 2, provider or graph code exists.

## Verification checkpoints

`tests/unit/sensorimotor/test_simulation_engine.py` covers full duration and all rates,
paused/runtime/manual behavior, midpoint/endpoints, zero-time/chained events, stable
same-time ordering, input/rate failure atomicity, initial overlap, swept tunneling,
crossing solid trajectories, robot collision/bounds, detached reads, reset and
save/restore during active motion with future/pending inputs. Existing contracts,
Tier 3 boundaries and A/B scenarios remain regression gates.
