"""I, Agent: Three-Layer Epistemic Architecture Dashboard.

An interactive Streamlit dashboard to explore, test, and audit:
- Tier 1: Declarative Belief Graph (NetworkX) and SQLite Provenance Ledger
- Tier 2: Procedural Intent Planning, ReAct execution, and Epistemic Evaluation
- Tier 3: Sensorimotor Environment Telemetry (LiDAR, Camera, Lighting, Robot Pose)
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from src.agent import AgentResponse, GroundedAgent
from src.contracts import FactAssertion, FactQuery, SpatialContext
from src.declarative.memory_repository import MemoryRepository
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import (
    AmbientLight,
    Obstacle,
    RobotState,
    SimulatedObject,
    WorldState,
)

st.set_page_config(
    page_title="I, Agent | GroundTruth Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


def get_or_create_agent() -> GroundedAgent:
    """Initialize or retrieve the GroundedAgent stored in session state."""
    if "agent" not in st.session_state:
        mem = MemoryRepository(":memory:")
        mem.add_alias("front route", "route_A")
        mem.add_alias("route a", "route_A")
        mem.add_alias("route_a", "route_A")
        mem.add_alias("the box", "box_01")
        mem.add_alias("red box", "box_01")
        mem.add_alias("blue box", "box_01")

        world = WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_101",
                frame_of_reference="robot_base",
            ),
            light=AmbientLight(intensity=1.0, color_cast="white"),
        )
        env = MockEnvironment(world=world)
        agent = GroundedAgent.create(memory=mem, environment=env)
        st.session_state.agent = agent
        st.session_state.history = []
    return st.session_state.agent


def seed_scenario_a(agent: GroundedAgent) -> None:
    """Set up Scenario A: Map clear vs. LiDAR blocked reality conflict."""
    # 1. Reset obstacles and add physical obstacle at 12cm ahead
    agent.environment.replace_world(
        WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_101",
                frame_of_reference="robot_base",
            ),
            obstacles={
                "obs_01": Obstacle(
                    obstacle_id="obs_01",
                    x_cm=0.0,
                    y_cm=12.0,
                    location="room_101",
                    active=True,
                )
            },
            light=AmbientLight(intensity=1.0, color_cast="white"),
        )
    )
    # 2. Seed memory assertion that map is clear
    now = datetime.now(UTC)
    agent.memory.record_fact(
        FactAssertion(
            subject="route_A",
            predicate="status_is",
            object="clear",
            source_agent="static_map",
            confidence_score=0.95,
            observed_at=now,
            context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            evidence={"map_version": "v1.0", "source": "building_blueprints"},
        )
    )
    st.session_state.selected_question = "Is the front route clear to move forward?"


def seed_scenario_b(agent: GroundedAgent) -> None:
    """Set up Scenario B: Perspective tracking (User vs. Egocentric vs. Historical)."""
    # 1. Physical object has intrinsic color red, ambient lighting is yellow
    agent.environment.replace_world(
        WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_101",
                frame_of_reference="robot_base",
            ),
            objects={
                "box_01": SimulatedObject(
                    object_id="box_01",
                    label="box",
                    intrinsic_color="red",
                    location="room_101",
                    x_cm=10.0,
                    y_cm=10.0,
                )
            },
            light=AmbientLight(intensity=0.8, color_cast="yellow"),
        )
    )
    # 2. Historical provenance: bot_02 logged that it painted the box blue
    now = datetime.now(UTC)
    agent.memory.record_fact(
        FactAssertion(
            subject="box_01",
            predicate="painted_color_is",
            object="blue",
            source_agent="bot_02",
            confidence_score=1.0,
            observed_at=now,
            context=SpatialContext(location="room_101"),
            evidence={"action": "painted_blue", "ticket_id": "MAINT-4091"},
        )
    )
    st.session_state.selected_question = (
        "Can you see the red box? What color do different perspectives think it is?"
    )


# ---------------------------------------------------------------------------
# Sidebar & Header
# ---------------------------------------------------------------------------
agent = get_or_create_agent()

st.sidebar.title("🤖 I, Agent Control Panel")
st.sidebar.markdown(
    """
**Three-Layer Epistemic Architecture**
- **Tier 1: Declarative Layer** (NetworkX Belief Graph & SQLite Provenance)
- **Tier 2: Procedural Layer** (ReAct Intent Planner & Epistemic Conflict Resolver)
- **Tier 3: Sensorimotor Layer** (LiDAR, Camera, Lighting & Physical World Sim)
"""
)

st.sidebar.subheader("🎯 Test Scenario Presets")
col_scen_a, col_scen_b = st.sidebar.columns(2)
with col_scen_a:
    if st.button("🚀 Load Scenario A\n(Map vs. LiDAR)", use_container_width=True):
        seed_scenario_a(agent)
        st.sidebar.success("Scenario A loaded!")

with col_scen_b:
    if st.button("👁️ Load Scenario B\n(Perspectives)", use_container_width=True):
        seed_scenario_b(agent)
        st.sidebar.success("Scenario B loaded!")

if st.sidebar.button("🔄 Reset Agent State", use_container_width=True):
    del st.session_state["agent"]
    if "selected_question" in st.session_state:
        del st.session_state["selected_question"]
    st.rerun()

st.title("🛡️ I, Agent: GroundTruth Cognitive System")
st.caption(
    "A decoupled 3-tier epistemic architecture verifying semantic groundedness, "
    "source provenance, and Theory of Mind perspective tracking."
)

# ---------------------------------------------------------------------------
# Main Query Input Section
# ---------------------------------------------------------------------------
default_q = st.session_state.get("selected_question", "Is the front route clear to move forward?")
user_query = st.text_input("💬 Ask the Cognitive Agent:", value=default_q)

execute_clicked = st.button("⚡ Execute Epistemic ReAct Cycle", type="primary")

response: AgentResponse | None = None
if execute_clicked and user_query:
    with st.spinner("Executing ReAct loop: planning -> observing -> evaluating..."):
        response = agent.ask(user_query)
        st.session_state.history.append(response)
elif st.session_state.history:
    response = st.session_state.history[-1]

# ---------------------------------------------------------------------------
# Dashboard Layout (Tabs)
# ---------------------------------------------------------------------------
tab_interaction, tab_tier1, tab_tier3 = st.tabs(
    [
        "🧠 Tier 2: Agent Reasoning & Outcome",
        "📚 Tier 1: Declarative Graph & Audit",
        "🕹️ Tier 3: Physical Environment",
    ]
)

# ---------------------------------------------------------------------------
# Tab 1: Agent Reasoning & Outcome
# ---------------------------------------------------------------------------
with tab_interaction:
    if response:
        status_colors = {
            "grounded_conflict_resolved": "red",
            "perspectives_tracked": "blue",
            "verified": "green",
            "unverifiable": "orange",
            "ambiguous": "violet",
            "unrecognized_entity": "gray",
            "audit_retrieved": "rainbow",
        }
        badge_color = status_colors.get(response.status, "gray")

        st.subheader("Agent Response")
        st.markdown(f'**Query**: *"{response.question}"*')
        st.markdown(f"**Epistemic Status**: :{badge_color}[**{response.status.upper()}**]")

        # Callout with explanation
        st.info(f"**Natural Language Synthesis**:\n\n{response.answer}")

        # Perspectives Breakdown (Scenario B)
        if response.perspectives:
            st.markdown("### 🎭 Theory of Mind: Perspective Tracking")
            p_cols = st.columns(3)
            with p_cols[0]:
                st.metric("1. User Perspective", "User Expectation")
                st.caption(response.perspectives.get("user_perspective", ""))
            with p_cols[1]:
                st.metric("2. Egocentric Perspective", "Live Sensor View")
                st.caption(response.perspectives.get("egocentric_perspective", ""))
            with p_cols[2]:
                st.metric("3. Historical Perspective", "Logged Provenance")
                st.caption(response.perspectives.get("historical_perspective", ""))

        # Atomic Belief Revisions
        if response.revisions:
            st.markdown("### ⚠️ Belief Revision Triggered")
            for rev in response.revisions:
                succ = rev.successor
                pred_id = rev.audit_event.input_fact_ids[0]
                st.warning(
                    f"**Revision Rule**: `{rev.audit_event.policy_rule}`\n\n"
                    f"**Reason**: {rev.audit_event.reason}\n\n"
                    f"**Predecessor Fact**: `{pred_id}` (superseded)\n\n"
                    f"**Successor Belief**: `{succ.subject} {succ.predicate} {succ.object}` "
                    f"(Source: `{succ.source_agent}`, Confidence: `{succ.confidence_score}`)"
                )

        # Telemetry extracted
        if response.sensor_telemetry:
            st.markdown("### 📡 Live Sensor Telemetry")
            st.json(response.sensor_telemetry)
    else:
        st.write("Click **Execute Epistemic ReAct Cycle** above to interact with the agent.")

# ---------------------------------------------------------------------------
# Tab 2: Tier 1 Knowledge Graph & Audit
# ---------------------------------------------------------------------------
with tab_tier1:
    st.subheader("Active Belief Graph (NetworkX MultiDiGraph)")
    current_graph = agent.graph.get_graph()

    g_col1, g_col2 = st.columns([1, 2])
    with g_col1:
        st.metric("Active Nodes", current_graph.number_of_nodes())
        st.metric("Active Edges", current_graph.number_of_edges())
        st.write("**Nodes**:", list(current_graph.nodes()))

    with g_col2:
        st.write("**Active Belief Edges**:")
        edge_data = []
        for u, v, key, data in current_graph.edges(keys=True, data=True):
            edge_data.append(
                {
                    "Subject": u,
                    "Predicate": data.get("predicate", ""),
                    "Object": v,
                    "Source": data.get("source_agent", ""),
                    "Confidence": data.get("confidence_score", 1.0),
                    "Fact ID": str(key)[:8] + "...",
                }
            )
        if edge_data:
            st.dataframe(edge_data, use_container_width=True)
        else:
            st.caption("No edges in active belief graph.")

    st.divider()
    st.subheader("Authoritative SQLite Evidence Ledger")
    all_facts = agent.memory.query_facts(FactQuery(active_only=False))

    fact_records = []
    for f in all_facts:
        fact_records.append(
            {
                "Fact ID": str(f.fact_id)[:8] + "...",
                "Subject": f.subject,
                "Predicate": f.predicate,
                "Object": f.object,
                "Source Agent": f.source_agent,
                "Confidence": f.confidence_score,
                "Active": "✅ Active"
                if f.superseded_by is None
                else f"❌ Superseded by {str(f.superseded_by)[:8]}...",
                "Observed At": f.observed_at.strftime("%H:%M:%S UTC"),
            }
        )
    st.dataframe(fact_records, use_container_width=True)

# ---------------------------------------------------------------------------
# Tab 3: Tier 3 Sensorimotor Environment
# ---------------------------------------------------------------------------
with tab_tier3:
    st.subheader("Physical World Simulation State")
    world = agent.environment._world

    w_col1, w_col2, w_col3 = st.columns(3)
    with w_col1:
        st.write("**Robot State**")
        st.json(world.robot.model_dump())

    with w_col2:
        st.write("**Ambient Lighting**")
        st.json(world.light.model_dump())

    with w_col3:
        st.write(f"**Obstacles ({len(world.obstacles)})**")
        st.json({k: obs.model_dump() for k, obs in world.obstacles.items()})

    st.write(f"**Objects ({len(world.objects)})**")
    st.json({k: obj.model_dump() for k, obj in world.objects.items()})
