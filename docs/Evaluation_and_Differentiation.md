# Evaluation and Differentiation Plan

## Why this document matters

Many teams can build a chat interface that calls a mock LiDAR sensor. That alone does not prove that an agent is grounded or handles conflicting knowledge correctly.

GroundTruth should stand out by making its reasoning **visible, reproducible, and testable**. The project should demonstrate not just an answer, but the complete chain behind the answer:

```text
incoming evidence
    -> selected/rejected evidence
    -> policy rule
    -> memory revision
    -> active graph update
    -> final grounded response
```

The goal is not feature quantity. A smaller system that visibly proves its conclusions is stronger than a large, unreliable LLM demo.

## How we can stand out

The project brief already asks every team to use memory, an LLM, and mock sensors. So having a LiDAR tool or saving audit logs alone will not make us different.

Our main idea is simple: **the LLM cannot just say what it thinks is true. The system must show the evidence and the rule used to choose an answer.**

| A simple project | Our project goal |
| --- | --- |
| LiDAR says “blocked”, so the chatbot answers “blocked”. | Keep both the old map claim and the new LiDAR reading. Show why LiDAR was trusted for the current situation. |
| The LLM decides which claim is true. | Python follows a fixed rule for conflicts. For example, a fresh LiDAR reading is safer than an old map for checking a route right now. |
| Save a log after giving the answer. | Saving the decision is part of changing the agent's belief. The answer comes after this step. |
| Show one working demo. | Test difficult cases such as unclear objects, unsupported questions, wrong sensor data, and bad LLM tool requests. |

### Simple project pitch

> GroundTruth does not only give an answer. It can show what it believed before, what it sensed now, why it trusted one source, and how its belief changed.

## How we prove an answer

Take the example: the map says a route is clear, but LiDAR finds an obstacle.

```text
Old memory:      route_A is clear according to the map
New observation: LiDAR finds an obstacle 12 cm ahead
Rule used:       current LiDAR is trusted over an old map for route safety
New belief:      route_A is blocked right now
Saved history:   old map fact is linked to the new LiDAR-based fact
Final answer:    “The map said clear, but LiDAR sees an obstacle now.”
```

This lets us answer these presentation questions:

| Question | What we can show |
| --- | --- |
| Which evidence did you use? | The map fact and the LiDAR reading, including source, time, confidence, and sensor data. |
| Why did you choose LiDAR? | The fixed safety rule: fresh direct sensor data is stronger than an old map for the current route. |
| What did you believe before? | The old map fact, which stays saved in SQLite even after the belief changes. |
| Can you show it again? | We replay the same starting facts and sensor readings in a new database and get the same result. |

Keeping the old fact is important. It proves that the agent really changed its mind because new evidence arrived.

## What the LLM does and what normal Python does

We will use the ReAct LLM loop required in the problem statement. The LLM has a limited job:

- understand the user's normal-language question;
- choose from the tools we allow, such as reading memory or LiDAR; and
- explain the final result in simple language.

Normal Python code handles the safety-critical work:

- check that the LLM asked for a real, allowed tool;
- get facts and sensor readings;
- compare conflicting claims using our fixed rules;
- save the belief change and its history; and
- give the LLM evidence it is allowed to use in the final answer.

If the LLM asks for an unavailable tool, gives an invalid request, or there is not enough evidence, the agent should say that it cannot verify the answer. It should not guess.

## The features that will actually make us stand out

1. **Fixed conflict rules**

   The LLM cannot freely choose between the map and a sensor. A clear rule chooses the safer evidence for the situation.

2. **Replay**

   We can run the same event sequence again from an empty database and show that the same kind of belief change happens again. This proves the result is not only because of hidden LLM text.

3. **Difficult test cases**

   We will test cases designed to make a chatbot fail: stale memory, two possible boxes, a reading from the wrong room, missing sensors, and invalid LLM tool calls. We can show a small pass/fail results table in the report.

4. **A live belief dashboard**

   When an obstacle is added in the mock environment, the dashboard should show the LiDAR value, the old map fact, the rule used, the saved history, and the updated graph. This makes the belief change easy to understand during the presentation.

### Optional extra feature: “What would change your mind?”

After the basic system works, the agent can answer questions such as:

> “What would make you think the route is clear again?”

For example: “A fresh LiDAR scan showing no obstacle inside the safety distance would make me check the route again.” This is a nice extra because it shows that the agent follows clear rules instead of just sounding confident.

## Evaluation principles

Every evaluation should answer these questions:

1. **Grounding:** Did the conclusion come from actual stored facts or current simulated observations?
2. **Provenance:** Can the system name the source, time, confidence, and evidence ID behind the conclusion?
3. **Conflict handling:** Did it compare contradictory claims rather than silently choose one?
4. **Reproducibility:** Do identical inputs produce the same belief state and result?
5. **Uncertainty:** Does it refuse or clarify when no reliable evidence exists?

The LLM's writing style is not the main thing being evaluated. The evidence and policy trace are.

## Core requirements before advanced work

Do not add advanced features until these work in deterministic tests.

### Scenario A: static map versus live obstacle

Setup:

```text
Tier 1 static map: route_A status_is clear, confidence 0.90
Tier 3 LiDAR: obstacle at 12 cm, confidence 0.99
```

Expected result:

```text
active belief: route_A is blocked
old map fact: preserved and linked to new LiDAR fact
audit event: explains fresh direct sensor policy
active graph: route_A -> blocked
answer: cites both claims and the reason for choosing LiDAR
```

What this proves: the system does not repeat historical map text when current evidence contradicts it.

### Scenario B: separate perspectives

Setup:

```text
User request: find a red box
Camera: box_01 appears brown under yellow light
Maintenance history: box_01 was painted blue yesterday
```

Expected result:

```text
user perspective: red target requested
current camera perception: brown under yellow light
historical maintenance claim: painted blue
```

What this proves: the agent does not combine incompatible source perspectives into an invented “true colour.”

### Unsupported-query behavior

Setup:

```text
User asks: “What is the room temperature?”
Environment has no temperature sensor.
Tier 1 has no temperature fact.
```

Expected result:

```text
uncertainty=true
no fabricated evidence IDs
answer: current temperature cannot be verified
```

What this proves: the agent refuses a claim rather than behaving like a general chatbot.

## Differentiator 1: deterministic replay

### Idea

Record the input sequence: seeded facts, sensor observations, policy decisions, and revisions. Replaying the same sequence should produce the same SQLite rows, active graph, and conclusion.

### Why it is impressive

An LLM-only system can give a different answer on a later run. A replayable belief system proves that its factual state is not hidden inside an unpredictable prompt.

### What to implement

Store or export a simple event fixture:

```json
[
  {"type": "fact", "source": "static_map", "subject": "route_A", "object": "clear"},
  {"type": "observation", "sensor": "lidar_sensor", "distance_cm": 12}
]
```

Run the fixture twice against a fresh in-memory SQLite database. Compare:

```text
selected active fact
audit event fields
revision predecessor/successor IDs or stable logical relationships
active graph edges
GroundedResult conclusion and policy rule
```

### Demo wording

> “Here is the original evidence timeline. We can replay it from an empty database and reproduce the same active belief and audit explanation.”

### Risk

Do not compare random UUID values directly unless IDs are deterministically seeded. Compare the logical facts and revision relationships instead.

## Differentiator 2: explainable policy trace

### Idea

Every belief revision should show exactly why one claim was selected over another.

### What to show

```text
Old claim: route_A clear
  source: static_map
  observed: 09:00
  confidence: 0.90

New claim: route_A blocked
  source: lidar_sensor
  observed: 10:00
  confidence: 0.99

Rule: fresh_direct_sensor_over_static_map
Decision: new claim becomes operational belief
Audit event: audit_123
```

### Why it is impressive

Most projects show only the final answer. This feature lets an evaluator inspect the decision, challenge it, and verify the evidence independently.

### Implementation rule

The policy trace must be structured data created by deterministic Python. Do not ask the LLM to invent an explanation after the fact. The LLM may turn the stored trace into friendlier language.

## Differentiator 3: adversarial test suite

### Idea

Test situations designed to make an LLM hallucinate, over-trust stale information, or confuse perspectives.

### Recommended scenarios

| Scenario | Correct behavior it proves |
| --- | --- |
| Old map says clear; fresh LiDAR says blocked | Live relevant evidence wins for current safety |
| Low-confidence camera observation in poor lighting | Agent states uncertainty or keeps separate evidence |
| “The box” refers to two boxes | Agent asks clarification instead of guessing |
| User asks for unsupported temperature/weather data | Agent refuses to verify it |
| LiDAR observation belongs to another room | Agent rejects it as wrong context |
| Obstacle appears, then moves away | History is preserved; active belief changes twice |
| LLM proposes `read_weather` | Capability validator rejects the imaginary tool |
| Malformed LLM JSON twice | Agent returns safe uncertainty, not free-text guess |
| Historical painted-blue and camera-brown claims | Perspectives remain distinct |
| Dashboard graph cache is rebuilt | Graph matches active SQLite facts |

### Why it is impressive

The test suite demonstrates that grounding is a property of the architecture, not a lucky output in one happy-path demo.

### Implementation tip

Write deterministic tests before adding the LLM provider. Use a fake intent provider that returns known `IntentRequest` objects. Later, separately test that the live LLM output is parsed and guarded correctly.

## Differentiator 4: counterfactual explanation

### Idea

Allow the user to ask:

> “What evidence would make you change your mind?”

### Example

```text
Current belief: route_A is blocked.
Reason: fresh LiDAR sees obstacle at 12 cm.

Counterfactual answer:
“A fresh LiDAR observation showing no obstacle within the configured
safety distance would cause the route status to be reconsidered.”
```

### Why it is impressive

It shows that the policy is explicit and understandable. The agent is not merely confident; it knows what conditions could revise its belief.

### How to implement safely

Generate the answer from the deterministic policy configuration, not an LLM guess. This is a later feature because the basic revision policy must work first.

## Differentiator 5: evidence dashboard

### Idea

Build a dashboard that makes all three layers visible during the demo.

### Minimum panels

```text
1. Mock environment: robot position, obstacle, box, lighting
2. Live sensor panel: raw LiDAR/camera JSON
3. Active graph: current NetworkX relationships
4. Evidence timeline: SQLite facts and audit events
5. Decision panel: selected evidence, conflict, policy rule, final answer
```

### Why it is impressive

An evaluator can see the exact moment when a sensor changes state, the old fact is superseded, and the graph changes. This makes the architecture much easier to trust than a chat transcript alone.

### Risk

Do not build the dashboard before the deterministic core is tested. A beautiful UI cannot compensate for unreliable belief logic.

## Differentiator 6: comparison with a naive baseline

### Idea

Run the same conflicting scenario through two modes:

```text
Baseline: answer using old map/context only
GroundTruth: use structured memory, live sensor, policy, and audit trail
```

### Demonstration

```text
Question: “Is route_A clear?”

Naive baseline: “Yes, the map says it is clear.”
GroundTruth: “No. The map said clear at 09:00, but LiDAR detected an
obstacle at 12 cm at 10:00. The active belief is now blocked.”
```

### Why it is impressive

It directly proves the problem statement: a normal language model can be fluent but ungrounded, while the proposed architecture has a concrete corrective mechanism.

### Important caution

Do not make unfair claims about a specific commercial model. Describe the baseline as a deliberately simplified map-only agent and evaluate both modes using the same seeded inputs.

## Optional advanced features

These are valuable only after the core suite passes.

### Per-predicate confidence decay

Dynamic facts should age differently from static facts:

```text
robot location       -> confidence decays quickly
temporary obstacle   -> decays moderately
wall/map structure   -> decays very slowly
```

This demonstrates temporal reasoning. The risk is poor tuning; always keep original assertions and show effective confidence separately.

### Compound questions

Example:

> “What colour is the box, and why do you believe that?”

The agent creates several sub-plans: camera observation, historical lookup, and audit explanation. It may answer supported parts while honestly flagging an unsupported part. The risk is additional planning complexity.

### Derived queries

Example:

> “What path did you actually take?”

This can be computed deterministically from historical `located_at` facts. Do not confuse this with “What was the optimal path?”, which needs an explicit route/cost graph and should be unsupported if that data is absent.

### RAG over documents

RAG can be useful for a long maintenance manual. Treat retrieved text as a lower-priority source with citation/provenance. It must not replace exact fact queries or live sensor evidence.

## Suggested grading demo sequence

```text
1. Start with route_A -> clear in active graph.
2. Show static-map fact in evidence timeline.
3. Add obstacle at 12 cm in the mock world.
4. Ask: “Is the route clear?”
5. Show raw LiDAR observation.
6. Show structured policy comparison and audit event.
7. Show graph change to route_A -> blocked.
8. Ask: “Why did you change your mind?”
9. Replay the event sequence from a fresh database.
10. Run one adversarial/unsupported question to show safe refusal.
```

## Priorities

Before November, prioritize in this order:

```text
Tier 1 correctness and tests
Scenario A and B
policy trace and audit timeline
adversarial tests
replay
dashboard
optional advanced features
```

The final message to evaluators should be simple:

> GroundTruth does not merely produce an answer. It can prove which evidence it used, why it preferred that evidence, how its memory changed, and how the same decision can be reproduced.
