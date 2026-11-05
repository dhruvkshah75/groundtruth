# P2-03 warehouse sensors

`WarehouseSensors` in `src/sensorimotor/warehouse_sensors.py` is the explicit Phase 2
adapter. Existing MockEnvironment construction, registry, request behavior, world
serialization and Scenario A/B payloads retain their legacy geometry. Only the mock
appearance rule is shared: red under yellow light appears brown.

## Headless engine example

```python
import json
from datetime import UTC, datetime
from pathlib import Path
from src.contracts import ObservationRequest, ScenarioDefinition
from src.sensorimotor.simulation_engine import SimulationEngine
from src.sensorimotor.warehouse_sensors import WarehouseSensors

fixtures = json.loads(Path("tests/fixtures/phase_two/contracts.json").read_text())
engine = SimulationEngine(
    ScenarioDefinition.model_validate(fixtures["valid"]["scenario"]),
    epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
)
sensors = WarehouseSensors(engine.snapshot)
request = ObservationRequest(capability="camera_detect", target="parcel_01")
initial = sensors.sample(request)
engine.play()
for _ in range(70):
    engine.advance()
changed = sensors.sample(request)
assert changed.sim_time_ms == 7000
assert changed.result.measurements["objects"][0]["position"] == {"x_cm": 150, "y_cm": 390}
for _ in range(150):
    engine.advance()
hidden = sensors.sample(request)
assert hidden.result.reason == "target_not_visible"
# The audience still sees parcel_01 in engine.snapshot(), but its position is absent
# from the sensor failure. The broad scan correctly retains visible parcel_02.
```

## Acquisition boundary

The constructor accepts a simulator-side snapshot supplier, an injectable UUID
factory and optionally a run-wide sequence factory `(run_id) -> int`. The supplier
is invoked exactly once per observe/sample call; its returned model is serialized
and revalidated as a detached copy before any measurements are computed. Pose,
objects, lighting, version, availability and timestamp therefore share one capture.
The supplier/application must serialize capture against engine mutation. This
adapter creates no locks, callbacks, scheduler, polling or autonomous robot actions.

`observe(request)` implements the existing SensorSource protocol and returns the
legacy success/unavailable union. `sample(request)` returns P2-01 ObservationSample,
with UUID sample identity even for unavailable sensors, run/time/sequence, capture
pose/version, original request and successful observation ID when applicable.

Capture time is `epoch_utc + sim_time_ms`, never a wall-clock read. Playback speed
never scales timestamps or measurements. Reads do not advance time or increment
world_version. One shared adapter per run owns default acquisition numbering; it
resets at a run change. Multiple adapters must share the injected sequence factory
and callers serialize access. `observe` supplies a bare result and does not allocate
sample sequence numbers; use `sample` for all ledger-bound acquisition attempts.
Reception/storage times, durable IDs after process restart and acquisition scheduling
are #24/P2-07 responsibilities. The adapter exposes no public world getter to the
agent; keep its snapshot supplier in simulator-side composition only.

## Geometry and measurements

The inventory camera detects **parcel-kind entities**. Shelves/cart are physical
geometry; stations and routes are annotations, not inventory detections. Targeted
and broad successes use CameraScan's sorted unique `objects` array; a targeted
success contains exactly the requested ID. Detection fields are ID, apparent color,
map-frame position estimate and existing width/height bounding-box fields only.
There are no intrinsic colors, occluder names, true hidden positions or future events
in returned evidence. Stable IDs and exact noise-free positions are mock assumptions.

Visibility checks room and visible flag, then center range and bearing relative to
cardinal pose, then line of sight. Field-of-view/range boundaries are included
(with 1e-9 numerical tolerance); touching an opaque rectangle edge/corner blocks the
ray. A parcel exactly at observer center has undefined projection; acquisition
returns a generic typed failure instead of an arbitrary bearing. All geometry is
validated finite by the P2-01 snapshot contract. Only the center is tested: partial
visibility, pixel imaging and learned tracking are outside scope.

Opaque footprints in the robot's room block sight, excluding the target itself.
Non-opaque stations never occlude. The merged scene contract has no `active` flag:
`visible` controls parcel detection, `opaque` controls occlusion, `solid` controls
LiDAR/physics; disabling an obstacle's sensing effects requires clearing its relevant
flags. Making a parcel invisible does not automatically make physical geometry
transparent. Other-room geometry is ignored.

Front LiDAR intersects a finite center ray with solid rectangular footprints and
reports the nearest surface distance, not center distance. Endpoint contact is a hit;
an origin within/on a solid footprint returns zero distance. Different-room and
non-solid footprints are ignored. No hit yields detected=false, null distance,
status=clear. A hit yields detected=true/status=blocked: this denotes a measurement,
not an emergency-stop or navigation authorization. The future patrol controller
applies its stopping threshold and freshness policy. Engine collision helpers use
open-interior/conservative contacts, so sensing uses its own focused inclusive slab
helper rather than incorrectly reusing physics contact rules.

Pose and ambient light come from that same snapshot with the same acquisition
metadata. Camera and LiDAR availability flags produce sensor_unavailable failures;
re-enabling them restores normal measurements.

## Failures, compatibility and limitations

All Phase 2 failures use `Observation unavailable.` with a typed reason. Unknown,
invisible, other-room, out-of-view, occluded and non-inventory targets all use the
same target_not_visible response; no hidden location or occluder is disclosed.
A healthy broad empty scan remains a successful `objects: []` reading, distinct
from an unavailable camera.

`observe` returns typed rejection for unsupported capabilities, non-camera targets
and unsupported parameters. `sample` can envelope unsupported-capability failures
but raises ValueError **before capture** for malformed known requests: the merged
P2-01 ObservationSample validator deliberately forbids those requests even when
results are failures. This keeps the original request honest and avoids weakening
the shared contract or silently sanitizing arguments. Use observe for that typed
rejection path, or validate known requests before submitting them for acquisition.
No broad exception handler hides internal programming errors.

No memory writes, belief revisions, source priority, route selection, confidence
decay, actual recognition, noise, robot middleware or UI is implemented here.
Schema validation cannot prove physical correctness; the tests check geometry.

## Verification and downstream handoff

`test_warehouse_sensors.py` checks four camera/LiDAR headings, FOV/range boundary
cases, occlusion and tangent contact, non-opaque/different-room geometry, nearest
surface, zero/no-hit results, target privacy, empty/outage distinction and recovery,
lighting, pose, identity/sequence, one-snapshot detachment and playback invariance.
Actual engine tests reproduce visible relocation, hidden parcel_01 while parcel_02
remains visible at 22 s, a clear LiDAR ray at 23 s, pause purity, reset numbering and
scripted outages/recovery. A separate engine-backed sequence observes parcel_01,
loses it behind shelf_01 at 21 s, moves it around the unchanged opaque shelf, and
reacquires it at (150,270) at 25 s with the same observer/run and later sample identity/time.
Legacy A/B and tier-boundary tests remain regression gates.

P2-07 should call sample on the simulation acquisition cadence, including failed
attempts, and pass returned evidence to #24/#27. P2-06 consumes front LiDAR and pose
samples without reading audience entities. None of those consumers should infer
an object's disappearance or current position from non-detection alone.
