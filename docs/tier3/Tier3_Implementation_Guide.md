# Tier 3 Implementation Guide

Tier 3 represents the robot's surroundings and supplies deterministic sensor observations. The current implementation is a mock environment for development and testing; it does not connect to physical hardware. Tier 2 requests observations through shared contracts, while Tier 3 does not query memory or decide what an observation means.

## World coordinates and context

The world stores robot and obstacle positions as `x_cm` and `y_cm`, measured in centimeters in a shared 2D world coordinate frame. The robot's `direction` is one of `north`, `east`, `south`, or `west`. Its `location` identifies the room, and `frame_of_reference` identifies the viewpoint used for the observation (for example, `robot_base`).

An observation includes a `SpatialContext`: room, frame of reference, robot identity as observer, and `world_version`. For example, a LiDAR result of 12 cm ahead means the nearest active obstacle is 12 cm along the robot's forward direction in the same room, in the world state identified by that version.

## Available sensor capabilities

| Capability | Result |
| --- | --- |
| `lidar_scan` | Checks for the nearest active obstacle in the requested direction. Only `direction: "front"` is currently supported. Obstacles in another room, behind the robot, inactive, or outside a 5 cm lateral tolerance are ignored. A clear result uses `detected: false`, `nearest_distance_cm: null`, and `status: "clear"`. |
| `camera_detect` | Returns one requested visible object or, when no target is given, all visible objects in the robot's room in stable object-ID order. It includes apparent color and bounding-box dimensions. Under a yellow color cast, an intrinsically red object appears brown in this mock model. |
| `ambient_light` | Returns the world's light intensity and color cast. |
| `robot_pose` | Returns robot ID, x/y coordinates, and cardinal direction; the context supplies room and frame. |

An unsupported capability, parameter, target use, direction, or unavailable camera target returns a typed `ObservationUnavailable` result. For example, asking `robot_pose` for a target object is rejected because that capability does not inspect objects.

## World changes and versioning

Observation calls do not mutate the world or increment its version. Test/configuration helpers for robot pose, obstacles, objects, and lighting increment `world_version` once when they change state. Replacing the complete world also advances from the current live version by exactly one; an incoming snapshot cannot reset or jump the live version. The next observation reports the new version in its context.

These helpers configure test scenarios. They are not advertised as robot actions and do not imply that the agent can physically move the robot or modify its environment.
