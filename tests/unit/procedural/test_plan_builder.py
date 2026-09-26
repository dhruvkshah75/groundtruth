"""Tests for deterministic typed plan construction."""

from src.contracts import CapabilityDescriptor, EntityResolution, IntentRequest
from src.procedural import CapabilityValidator, PlanBuilder


class FakeEntityResolver:
    """Return pre-arranged entity results and record resolved mentions."""

    def __init__(self, results: dict[str, EntityResolution]) -> None:
        """Store results keyed by entity mention.

        Args:
            results: Entity results keyed by user-facing mentions.
        """
        self.results = results
        self.mentions: list[str] = []

    def resolve_entity(self, mention: str) -> EntityResolution:
        """Return the configured result for one mention.

        Args:
            mention: The entity mention to resolve.

        Returns:
            The configured entity result.
        """
        self.mentions.append(mention)
        return self.results[mention]


def sensor(name: str) -> CapabilityDescriptor:
    """Create a sensor descriptor for a test.

    Args:
        name: The advertised sensor capability name.

    Returns:
        A valid sensor descriptor.
    """
    return CapabilityDescriptor(name=name, kind="sensor", description=f"Test {name} sensor.")


def resolved_builder(capabilities: list[CapabilityDescriptor]) -> PlanBuilder:
    """Create a builder with resolved route and object names.

    Args:
        capabilities: The advertised test capabilities.

    Returns:
        A builder using a fake resolver and fixed registry.
    """
    resolver = FakeEntityResolver(
        {
            "front route": EntityResolution(
                mention="front route",
                status="resolved",
                canonical_entity_id="route_ahead",
            ),
            "the box": EntityResolution(
                mention="the box",
                status="resolved",
                canonical_entity_id="box_01",
            ),
        }
    )
    return PlanBuilder(resolver, CapabilityValidator(capabilities))


def test_route_status_requires_active_memory_and_lidar() -> None:
    builder = resolved_builder([sensor("lidar_scan")])

    outcome = builder.build(
        IntentRequest(
            intent="current_route_status",
            entity_mentions=["front route"],
            user_question="Can I move forward?",
        )
    )

    assert outcome.plan.memory_operations[0].query.subject == "route_ahead"
    assert outcome.plan.memory_operations[0].query.predicate == "status_is"
    assert outcome.plan.memory_operations[0].query.active_only is True
    assert outcome.plan.observation_operations[0].request.capability == "lidar_scan"
    assert outcome.plan.current_state_verifiable is True


def test_route_status_without_lidar_is_unverifiable_but_keeps_memory_context() -> None:
    builder = resolved_builder([])

    outcome = builder.build(
        IntentRequest(
            intent="current_route_status",
            entity_mentions=["front route"],
            user_question="Is the route clear?",
        )
    )

    assert len(outcome.plan.memory_operations) == 1
    assert outcome.plan.observation_operations == []
    assert outcome.plan.current_state_verifiable is False
    assert outcome.plan.blocking_reasons


def test_object_perception_requires_camera() -> None:
    builder = resolved_builder([sensor("camera_detect")])

    outcome = builder.build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour does the box look like now?",
        )
    )

    assert outcome.plan.observation_operations[0].request.capability == "camera_detect"
    assert outcome.plan.observation_operations[0].request.target == "box_01"


def test_object_perception_without_camera_is_unverifiable() -> None:
    builder = resolved_builder([])

    outcome = builder.build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour does the box look like now?",
        )
    )

    assert outcome.plan.observation_operations == []
    assert outcome.plan.current_state_verifiable is False
    assert outcome.plan.blocking_reasons


def test_historical_lookup_uses_history_and_no_sensor() -> None:
    builder = resolved_builder([sensor("camera_detect")])

    outcome = builder.build(
        IntentRequest(
            intent="historical_fact_lookup",
            entity_mentions=["the box"],
            user_question="What did you know about the box earlier?",
        )
    )

    assert outcome.plan.memory_operations[0].query.active_only is False
    assert outcome.plan.observation_operations == []


def test_audit_explanation_creates_only_an_audit_operation() -> None:
    builder = resolved_builder([])

    outcome = builder.build(
        IntentRequest(
            intent="audit_explanation",
            entity_mentions=["the box"],
            user_question="Why do you believe this about the box?",
        )
    )

    assert outcome.plan.memory_operations[0].query.subject == "box_01"
    assert outcome.plan.memory_operations[0].query.active_only is True
    assert outcome.plan.audit_operations[0].fact_ids_from_memory_operation == 0
    assert "every fact" in outcome.plan.audit_operations[0].purpose
    assert outcome.plan.observation_operations == []


def test_robot_pose_requires_robot_pose_sensor() -> None:
    builder = resolved_builder([sensor("robot_pose")])

    outcome = builder.build(
        IntentRequest(intent="current_robot_pose", user_question="Where are you now?")
    )

    assert outcome.plan.observation_operations[0].request.capability == "robot_pose"
    assert outcome.plan.current_state_verifiable is True


def test_robot_pose_without_sensor_is_unverifiable() -> None:
    builder = resolved_builder([])

    outcome = builder.build(
        IntentRequest(intent="current_robot_pose", user_question="Where are you now?")
    )

    assert outcome.plan.observation_operations == []
    assert outcome.plan.current_state_verifiable is False


def test_wrong_sensor_kind_is_invalid_configuration() -> None:
    builder = resolved_builder(
        [
            CapabilityDescriptor(
                name="lidar_scan", kind="action", description="Incorrect test setup."
            )
        ]
    )

    outcome = builder.build(
        IntentRequest(
            intent="current_route_status",
            entity_mentions=["front route"],
            user_question="Can I move forward?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "invalid_configuration"


def test_ambiguous_entity_returns_clarification_without_operations() -> None:
    resolver = FakeEntityResolver(
        {
            "the box": EntityResolution(
                mention="the box",
                status="ambiguous",
                candidates=["box_01", "box_02"],
            )
        }
    )
    builder = PlanBuilder(resolver, CapabilityValidator([sensor("camera_detect")]))

    outcome = builder.build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour is the box?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "ambiguous_entity"
    assert outcome.fallback.clarification_candidates == ["box_01", "box_02"]


def test_missing_entity_returns_no_plan() -> None:
    resolver = FakeEntityResolver(
        {"unknown": EntityResolution(mention="unknown", status="missing")}
    )
    builder = PlanBuilder(resolver, CapabilityValidator([]))

    outcome = builder.build(
        IntentRequest(
            intent="historical_fact_lookup",
            entity_mentions=["unknown"],
            user_question="What did you know about unknown?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "missing_entity"


def test_blank_resolved_entity_id_returns_safe_configuration_fallback() -> None:
    resolver = FakeEntityResolver(
        {
            "the box": EntityResolution(
                mention="the box",
                status="resolved",
                canonical_entity_id="",
            )
        }
    )
    builder = PlanBuilder(resolver, CapabilityValidator([sensor("camera_detect")]))

    outcome = builder.build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour is the box?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "invalid_configuration"


def test_whitespace_padded_resolved_entity_id_returns_safe_fallback() -> None:
    resolver = FakeEntityResolver(
        {
            "the box": EntityResolution(
                mention="the box",
                status="resolved",
                canonical_entity_id=" box_01 ",
            )
        }
    )
    outcome = PlanBuilder(resolver, CapabilityValidator([sensor("camera_detect")])).build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour is the box?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "invalid_configuration"


def test_duplicate_ambiguous_entity_candidates_return_safe_configuration_fallback() -> None:
    resolver = FakeEntityResolver(
        {
            "the box": EntityResolution(
                mention="the box",
                status="ambiguous",
                candidates=["box_01", "box_01"],
            )
        }
    )
    builder = PlanBuilder(resolver, CapabilityValidator([sensor("camera_detect")]))

    outcome = builder.build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour is the box?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "invalid_configuration"


def test_blank_ambiguous_entity_candidate_returns_safe_configuration_fallback() -> None:
    resolver = FakeEntityResolver(
        {
            "the box": EntityResolution(
                mention="the box",
                status="ambiguous",
                candidates=["box_01", ""],
            )
        }
    )
    builder = PlanBuilder(resolver, CapabilityValidator([sensor("camera_detect")]))

    outcome = builder.build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour is the box?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "invalid_configuration"


def test_whitespace_padded_ambiguous_candidate_returns_safe_fallback() -> None:
    resolver = FakeEntityResolver(
        {
            "the box": EntityResolution(
                mention="the box",
                status="ambiguous",
                candidates=["box_01", " box_01 "],
            )
        }
    )
    outcome = PlanBuilder(resolver, CapabilityValidator([sensor("camera_detect")])).build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour is the box?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "invalid_configuration"


def test_resolved_result_with_candidates_returns_safe_fallback() -> None:
    resolver = FakeEntityResolver(
        {
            "the box": EntityResolution(
                mention="the box",
                status="resolved",
                canonical_entity_id="box_01",
                candidates=["box_02", "box_03"],
            )
        }
    )
    outcome = PlanBuilder(resolver, CapabilityValidator([sensor("camera_detect")])).build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box"],
            user_question="What colour is the box?",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "invalid_configuration"


def test_multiple_entity_mentions_do_not_choose_the_first() -> None:
    builder = resolved_builder([sensor("camera_detect")])

    outcome = builder.build(
        IntentRequest(
            intent="current_object_perception",
            entity_mentions=["the box", "front route"],
            user_question="Compare the box and route.",
        )
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "unsupported_intent"


def test_unsupported_intent_creates_no_plan() -> None:
    builder = resolved_builder([sensor("lidar_scan")])

    outcome = builder.build(
        IntentRequest(intent="unsupported", user_question="What is the room temperature?")
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "unsupported_intent"


def test_environment_action_creates_no_plan_without_a_safe_action_name() -> None:
    builder = resolved_builder([])

    outcome = builder.build(
        IntentRequest(intent="environment_action", user_question="Move forward.")
    )

    assert outcome.plan is None
    assert outcome.fallback.category == "unsupported_intent"
