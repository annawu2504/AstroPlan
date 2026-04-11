"""Unit tests for SubTreeReplanner — subtree rebuild and parameter adjustment (P3)."""
from unittest.mock import MagicMock

import pytest

from src.cognition.replanner import ReplanContext, SubTreeReplanner
from src.types import AgentDecision, EventTriggerSignal, SharedContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal(source: str = "telemetry_deviation", priority: int = 5,
            preemptive: bool = True) -> EventTriggerSignal:
    return EventTriggerSignal(source=source, priority=priority, preemptive=preemptive)


def _context(telemetry: dict = None) -> SharedContext:
    return SharedContext(lab_id="test-lab", telemetry=telemetry or {})


def _thresholds() -> dict:
    return {
        "temperature": {"min": 10.0, "max": 50.0, "severity": "WARNING"},
        "pressure":    {"min": 0.5,  "max": 2.0,  "severity": "CRITICAL"},
    }


def _mock_agent(skill: str = "heat_to_40", params: dict = None) -> MagicMock:
    """AgentNode mock that always returns an Act decision."""
    agent = MagicMock()
    agent.execute_decision.return_value = AgentDecision(
        skill="Act",
        action={"skill": skill, "params": params or {"target": 40}},
    )
    return agent


# ---------------------------------------------------------------------------
# Basic replan behaviour
# ---------------------------------------------------------------------------

def test_replan_without_agent_returns_noop_fallback():
    r = SubTreeReplanner()
    ctx = _replan(r, goals=["cool_down"])
    assert len(ctx.new_plan) == 1
    assert ctx.new_plan[0]["skill"] == "noop"
    assert ctx.conflict_resolved is True


def test_replan_with_agent_returns_act_steps():
    r = SubTreeReplanner(agent_node=_mock_agent())
    ctx = _replan(r, goals=["heat_to_40"])
    assert len(ctx.new_plan) == 1
    assert ctx.new_plan[0]["skill"] == "heat_to_40"
    assert ctx.conflict_resolved is True


def test_replan_multiple_goals_generates_one_step_per_goal():
    agent = MagicMock()
    agent.execute_decision.side_effect = [
        AgentDecision(skill="Act", action={"skill": "step_a", "params": {}}),
        AgentDecision(skill="Act", action={"skill": "step_b", "params": {}}),
    ]
    r = SubTreeReplanner(agent_node=agent)
    ctx = _replan(r, goals=["goal_a", "goal_b"])
    assert len(ctx.new_plan) == 2
    assert ctx.new_plan[0]["skill"] == "step_a"
    assert ctx.new_plan[1]["skill"] == "step_b"


def test_think_decision_produces_no_plan_entry():
    """If the agent returns Think instead of Act, no step is added."""
    agent = MagicMock()
    agent.execute_decision.return_value = AgentDecision(skill="Think", action={})
    r = SubTreeReplanner(agent_node=agent)
    ctx = _replan(r, goals=["some_goal"])
    assert len(ctx.new_plan) == 0
    assert ctx.conflict_resolved is False


def test_depth_limit_returns_empty_plan():
    r = SubTreeReplanner(max_depth=2, agent_node=_mock_agent())
    ctx = _replan(r, goals=["goal"], depth=2)
    assert ctx.new_plan == []
    assert ctx.conflict_resolved is False


def test_replan_count_increments_each_call():
    r = SubTreeReplanner(agent_node=_mock_agent())
    assert r.replan_count == 0
    _replan(r, goals=["g1"])
    assert r.replan_count == 1
    _replan(r, goals=["g2"])
    assert r.replan_count == 2


def test_depth_limit_does_not_increment_count():
    """Hitting max_depth short-circuits before the counter is touched."""
    r = SubTreeReplanner(max_depth=1, agent_node=_mock_agent())
    _replan(r, goals=["g"], depth=1)   # depth == max_depth → no increment
    assert r.replan_count == 0


# ---------------------------------------------------------------------------
# _derive_param_overrides — unit tests
# ---------------------------------------------------------------------------

def test_no_overrides_when_telemetry_within_bounds():
    r = SubTreeReplanner(thresholds=_thresholds())
    overrides = r._derive_param_overrides({"temperature": 30.0, "pressure": 1.0})
    assert overrides == {}


def test_max_breach_sets_value_to_90_percent_of_threshold():
    r = SubTreeReplanner(thresholds=_thresholds())
    overrides = r._derive_param_overrides({"temperature": 60.0})  # max=50
    assert "temperature" in overrides
    assert abs(overrides["temperature"] - 50.0 * 0.9) < 1e-6


def test_min_breach_sets_value_to_110_percent_of_threshold():
    r = SubTreeReplanner(thresholds=_thresholds())
    overrides = r._derive_param_overrides({"pressure": 0.2})  # min=0.5
    assert "pressure" in overrides
    assert abs(overrides["pressure"] - 0.5 * 1.1) < 1e-6


def test_multiple_simultaneous_breaches_all_returned():
    r = SubTreeReplanner(thresholds=_thresholds())
    overrides = r._derive_param_overrides({"temperature": 80.0, "pressure": 0.1})
    assert "temperature" in overrides
    assert "pressure" in overrides


def test_missing_telemetry_key_produces_no_override():
    r = SubTreeReplanner(thresholds=_thresholds())
    overrides = r._derive_param_overrides({})   # no readings at all
    assert overrides == {}


def test_non_numeric_telemetry_value_is_skipped():
    r = SubTreeReplanner(thresholds=_thresholds())
    overrides = r._derive_param_overrides({"temperature": "N/A"})
    assert overrides == {}


def test_no_thresholds_configured_returns_empty():
    r = SubTreeReplanner(thresholds={})
    overrides = r._derive_param_overrides({"temperature": 99.9})
    assert overrides == {}


# ---------------------------------------------------------------------------
# Parameter adjustment integrated into replan()
# ---------------------------------------------------------------------------

def test_telemetry_trigger_merges_overrides_into_agent_params():
    """Param overrides must appear in the generated plan step's params dict."""
    r = SubTreeReplanner(
        agent_node=_mock_agent(params={"target": 40}),
        thresholds=_thresholds(),
    )
    ctx = _replan(
        r,
        goals=["cool_down"],
        source="telemetry_deviation",
        telemetry={"temperature": 70.0},   # breaches max=50 → override 45.0
    )
    assert len(ctx.new_plan) == 1
    assert "temperature" in ctx.new_plan[0]["params"]
    assert ctx.param_overrides != {}


def test_non_telemetry_trigger_produces_no_overrides():
    r = SubTreeReplanner(
        agent_node=_mock_agent(params={"target": 40}),
        thresholds=_thresholds(),
    )
    ctx = _replan(
        r,
        goals=["some_goal"],
        source="ground_command",
        telemetry={"temperature": 70.0},   # breached but trigger is not telemetry
    )
    assert ctx.param_overrides == {}


def test_telemetry_trigger_fallback_noop_contains_overrides():
    """Even the noop fallback path must carry the param overrides."""
    r = SubTreeReplanner(thresholds=_thresholds())   # no agent_node
    ctx = _replan(
        r,
        goals=["recover"],
        source="telemetry_deviation",
        telemetry={"pressure": 3.0},   # breaches max=2.0
    )
    assert "pressure" in ctx.new_plan[0]["params"]
    assert ctx.param_overrides != {}


def test_replan_context_stores_param_overrides():
    r = SubTreeReplanner(thresholds=_thresholds())
    ctx = _replan(
        r,
        goals=["g"],
        source="telemetry_deviation",
        telemetry={"temperature": 60.0},
    )
    assert isinstance(ctx.param_overrides, dict)
    assert "temperature" in ctx.param_overrides


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _replan(
    replanner: SubTreeReplanner,
    goals: list,
    source: str = "telemetry_deviation",
    telemetry: dict = None,
    depth: int = 0,
) -> ReplanContext:
    return replanner.replan(
        trigger=_signal(source=source),
        failed_step=None,
        context=_context(telemetry=telemetry or {}),
        remaining_goals=goals,
        depth=depth,
    )
