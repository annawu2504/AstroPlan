"""Unit tests for AgentNode — mock planner paths, plan_mode dispatch."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cognition.agent_node import AgentNode
from src.control.dag_builder import DAGBuilder
from src.types import NodeRunContext, ResumeSignal, SharedContext


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _ctx() -> SharedContext:
    return SharedContext(lab_id="test", telemetry={}, subsystem_states={}, action_log=[])


def _make_env(plan_mode: bool = True) -> MagicMock:
    env = MagicMock()
    env.plan_mode = plan_mode
    env.lab_id = "test"
    env._dag = DAGBuilder(revision_id="rev_test")
    env._memory.snapshot.return_value = _ctx()
    env._skill_to_subsystem.return_value = "test_subsystem"
    return env


def _rctx(env: MagicMock = None) -> NodeRunContext:
    return NodeRunContext(
        context=_ctx(),
        log=[],
        max_depth=5,
        env=env or _make_env(),
    )


# ------------------------------------------------------------------
# _mock_plan — Case 1: exact skill match
# ------------------------------------------------------------------

def test_mock_plan_exact_skill_match_returns_act():
    agent = AgentNode(
        node_id="test",
        llm_client=None,
        available_skills={"activate_pump": "start the pump"},
    )
    decision = agent.execute_decision("activate_pump", _ctx(), [])
    assert decision.skill == "Act"
    assert decision.action["skill"] == "activate_pump"


def test_mock_plan_exact_skill_match_case_sensitive():
    """Skill names are exact — 'Activate_pump' ≠ 'activate_pump'."""
    agent = AgentNode(node_id="t", llm_client=None,
                      available_skills={"activate_pump": ""})
    decision = agent.execute_decision("Activate_pump", _ctx(), [])
    # Not an exact match — should Expand or Think, not Act with wrong case
    assert not (decision.skill == "Act" and decision.action.get("skill") == "Activate_pump")


# ------------------------------------------------------------------
# _mock_plan — Case 2: compound " and " goal
# ------------------------------------------------------------------

def test_mock_plan_compound_and_goal_expands():
    agent = AgentNode(node_id="t", llm_client=None,
                      available_skills={"heat_to_40": "", "cool_down": ""})
    decision = agent.execute_decision("heat_to_40 and cool_down", _ctx(), [])
    assert decision.skill == "Expand"
    subgoals = decision.action.get("subgoals", [])
    assert "heat_to_40" in subgoals
    assert "cool_down" in subgoals


# ------------------------------------------------------------------
# _mock_plan — Case 3: high-level goal → Expand remaining skills
# ------------------------------------------------------------------

def test_mock_plan_high_level_expands_all_skills():
    skills = {"activate_pump": "", "heat_to_40": "", "activate_camera": ""}
    agent = AgentNode(node_id="t", llm_client=None, available_skills=skills)
    decision = agent.execute_decision("perform full experiment", _ctx(), [])
    assert decision.skill == "Expand"
    assert len(decision.action.get("subgoals", [])) == 3


def test_mock_plan_excludes_emergency_skills_in_normal_goal():
    skills = {"activate_pump": "", "emergency_stop": ""}
    agent = AgentNode(node_id="t", llm_client=None, available_skills=skills)
    decision = agent.execute_decision("run experiment", _ctx(), [])
    assert decision.skill == "Expand"
    assert "emergency_stop" not in decision.action.get("subgoals", [])


def test_mock_plan_includes_emergency_skills_in_emergency_goal():
    skills = {"activate_pump": "", "emergency_stop": ""}
    agent = AgentNode(node_id="t", llm_client=None, available_skills=skills)
    decision = agent.execute_decision("emergency abort", _ctx(), [])
    assert decision.skill in ("Act", "Expand")
    # emergency_stop should be preferred
    if decision.skill == "Act":
        assert decision.action.get("skill") == "emergency_stop"
    else:
        assert "emergency_stop" in decision.action.get("subgoals", [])


def test_mock_plan_skips_already_completed_skills():
    skills = {"a": "", "b": "", "c": ""}
    ctx = SharedContext(
        lab_id="test",
        action_log=[{"skill": "a", "params": {}, "subsystem": ""}],
    )
    agent = AgentNode(node_id="t", llm_client=None, available_skills=skills)
    decision = agent.execute_decision("run all", ctx, [])
    assert decision.skill == "Expand"
    assert "a" not in decision.action.get("subgoals", [])


def test_mock_plan_default_skills_used_when_no_catalog():
    agent = AgentNode(node_id="t", llm_client=None, available_skills={})
    decision = agent.execute_decision("do something", _ctx(), [])
    assert decision.skill == "Expand"


# ------------------------------------------------------------------
# _execute_action — plan_mode=True (dry-run)
# ------------------------------------------------------------------

def test_execute_action_plan_mode_registers_in_dag():
    env = _make_env(plan_mode=True)
    agent = AgentNode(node_id="test", llm_client=None)

    result = asyncio.run(
        agent._execute_action({"skill": "activate_pump", "params": {}}, _rctx(env))
    )

    assert result is True
    assert env._dag.node_count() == 1


def test_execute_action_plan_mode_does_not_call_mcp():
    env = _make_env(plan_mode=True)
    agent = AgentNode(node_id="test", llm_client=None)

    asyncio.run(
        agent._execute_action({"skill": "activate_pump", "params": {}}, _rctx(env))
    )

    env._mcp.call.assert_not_called()


def test_execute_action_plan_mode_logs_planned():
    env = _make_env(plan_mode=True)
    log: list = []
    rctx = NodeRunContext(context=_ctx(), log=log, max_depth=5, env=env)
    agent = AgentNode(node_id="test", llm_client=None)

    asyncio.run(agent._execute_action({"skill": "heat_to_40", "params": {}}, rctx))

    assert any(e.get("status") == "planned" for e in log)


# ------------------------------------------------------------------
# _execute_action — plan_mode=False, interlock failure
# ------------------------------------------------------------------

def test_execute_action_interlock_failure_returns_false():
    from src.physics.interlock_engine import InterlockViolation

    env = _make_env(plan_mode=False)
    env._interlock.validate_action.side_effect = InterlockViolation("blocked")
    agent = AgentNode(node_id="test", llm_client=None)
    log: list = []
    rctx = NodeRunContext(context=_ctx(), log=log, max_depth=5, env=env)

    result = asyncio.run(
        agent._execute_action({"skill": "unsafe_skill", "params": {}}, rctx)
    )

    assert result is False
    assert any(e.get("status") == "failed" for e in log)


# ------------------------------------------------------------------
# P1-A regression placeholder — HITL not yet wired (xfail strict=True)
# ------------------------------------------------------------------

def test_execute_action_non_interruptible_hitl_reject():
    """P1-A: a non-interruptible skill with HITL rejection must return False."""
    env = _make_env(plan_mode=False)
    env._interlock.validate_action.return_value = None  # passes validation
    env._hitl.suspend = AsyncMock(return_value=ResumeSignal(approved=False))
    env._latency.record_from_telemetry.return_value = None

    agent = AgentNode(node_id="test", llm_client=None)
    rctx = _rctx(env)

    # execute_main_forming is designated non-interruptible (per P1-A spec)
    result = asyncio.run(
        agent._execute_action({"skill": "execute_main_forming", "params": {}}, rctx)
    )

    assert result is False, "HITL rejection must cause _execute_action to return False"
    env._hitl.suspend.assert_awaited_once()


# Canonical name required by the P0-B checklist — delegates to the xfail stub above.
test_interruptible_false_rejects_on_hitl = test_execute_action_non_interruptible_hitl_reject
