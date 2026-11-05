# GroundTruth Documentation Guide

## Start here

1. [Project Study Guide](presentation/Project_Study_Guide.md) - detailed presentation-ready explanation of the complete flow, tiers, scenarios, graph contents, team contributions, milestone status and likely questions.
2. [Three-Slide Presentation Outline](presentation/Three_Slide_Presentation_Outline.md) - concise slide content and speaker handoffs for the team presentation.
3. [Current Agent and Scenario Guide](Implemented_System_Overview.md) - beginner-friendly explanation of the running agent, live/offline provider modes, frontend/API flow, and complete Scenario A/B walkthroughs.
4. [Architecture Decisions](Architecture_Decisions.md) - the current implementation decisions for Tier 1 and Tier 2.
5. [Tier 1 Implementation Guide](tier1/Tier1_Implementation_Guide.md) - SQLite facts, revisions, aliases, and NetworkX cache.
6. [Tier 2 Implementation Guide](tier2/Tier2_Implementation_Guide.md) - intent classification, deterministic planning, policy, and guardrails.
7. [Tier 3 Implementation Guide](tier3/Tier3_Implementation_Guide.md) - the mock world, sensor behavior, spatial context, and deterministic test helpers.
8. [Evaluation and Differentiation Plan](Evaluation_and_Differentiation.md) - how the project proves reliability and stands out in grading.
9. [Phase Two: Dynamic Simulation](phase-two/Dynamic_Simulation_Plan.md) - proposed time-aware simulation, sensing, memory, and integration milestones.
10. [Phase Two: Demo and UI](phase-two/Simulation_Demo_UI.md) - proposed slow-playback visual demo: 30 simulation seconds, approximately one presentation minute at default 0.5×.
11. [Phase Two: Implementation Roadmap Draft](phase-two/Implementation_Roadmap_Draft.md) - discussion draft covering implementation decisions, team lanes, milestones, and 14 proposed GitHub issues; decisions remain pending.

## Phase 2 implementation reference

- [Shared simulation contracts (P2-01)](phase-two/Shared_Simulation_Contracts.md) - validated types, warehouse definition, shared fixtures, timing semantics, and downstream integration handoff.

- [Simulation engine (P2-02)](phase-two/Simulation_Engine.md) - explicit clock, motion, collisions, lifecycle and replay checkpoints.

## Supporting context

- [agent Masterplan](I_Agent_Masterplan.md) explains the original assignment vision and three-tier motivation.
- [Potential Issues vs Alternatives](Potential%20Issues%20vs%20Alternatives.md) records risks and alternatives considered before selecting the current design.
- [Local Development Setup](Local_Development_Setup.md) covers the `uv` workflow.
- [GT-07 / Issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17) tracks the completed LLM function-calling/ReAct milestone and its implementation context.

The documents in “Start here” take precedence if an older supporting document differs from the current implementation approach.
