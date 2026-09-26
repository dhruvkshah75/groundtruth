# GroundTruth: Epistemic Agent Architecture

A three-layer cognitive system for grounded agents that validates beliefs against sensor reality and tracks perspective.

## Architecture

GroundTruth separates language understanding, stored evidence, and live
observations into three tiers:

```text
User question
    ↓
Tier 2: classify the request, choose the evidence needed, and apply policy
   ↙                                                               ↘
Tier 1: store and retrieve facts, history, and audit records    Tier 3: provide
                                                               sensor readings
```

Tier 1 is the long-term evidence store. It keeps facts with their sources,
observation times, confidence, and revision history. SQLite is the source of
truth; the active NetworkX graph is a derived view for relationship queries.

Tier 3 represents the robot's environment. It advertises available sensors
and permitted actions, and supplies observations such as LiDAR or camera
results. Tier 1 and Tier 3 do not call each other; Tier 2 coordinates requests
between them.

### How Tier 2 plans a request

The LLM helps map flexible wording to one approved intent, such as
`current_route_status` or `historical_fact_lookup`. Python validates that
intent, resolves entity names to canonical IDs, checks available capabilities,
and builds a typed evidence plan. The LLM does not invent database queries,
sensors, or final facts.

For example, “Can I move forward?” can become a route-status intent. Tier 2
then plans an active route-status lookup in Tier 1 and a fresh LiDAR
observation when LiDAR is available. A later executor performs those
operations; the plan itself contains requests, not readings or conclusions.

If the provider returns a valid `unsupported` intent, Tier 2 checks a small
set of documented wording rules. One supported candidate may receive one
constrained provider reconsideration. No candidate produces a safe unsupported
result; several candidates produce clarification choices. These fallback
paths create no evidence operations and make no claim about the environment.


