# Future Work

This note records two improvements identified after the current Scenario A and
Scenario B implementation. They are design directions for the next iteration,
not behaviour implemented by the current demo.

## 1. Confidence-aware belief updates

Current confidence values are metadata configured with stored facts and mock
observations. The route policy currently gives a fresh, context-compatible
LiDAR conflict priority over an older static-map claim.

The next version should make confidence useful in the update decision:

- let an unconfirmed stored belief lose effective confidence as it becomes old;
- model observation quality instead of treating every mock sensor reading as
  perfectly certain;
- distinguish immediate safety from permanent belief revision: a possible
  obstacle can stop the robot immediately, while a low-quality isolated scan
  can request confirmation before replacing the long-term route belief;
- strengthen or refresh a belief when fresh observations agree with it.

The exact decay formula, threshold, observation schedule, and number of
confirming readings remain open design choices. A background timer may be one
way to refresh confidence, but it should be evaluated alongside decay that is
calculated when a belief is read or compared.

## 2. Context-aware resolution of relative entities

The current controlled demo uses a static alias such as `front route ->
route_A`. This is sufficient for Scenario A, which examines one predefined
route, but it does not scale to a world with several routes or a moving robot.

The next version should keep stable aliases such as `red box -> box_01`, while
resolving relative phrases such as `front route`, `route to my left`, or
`nearest exit` at query time. The resolver should combine:

- the robot's current pose from Tier 3;
- route topology and locations stored in Tier 1; and
- deterministic spatial rules in Tier 2.

For example, after the robot turns east, `front route` should resolve to the
eastward connected route rather than continuing to mean a fixed `route_A`.
