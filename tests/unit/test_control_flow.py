"""Unit tests for ControlFlowNode — Sequence, Fallback, Parallel semantics.

Includes the P0-A regression test: inline-replanned AgentNode instances
must receive the environment's skill catalog, not an empty dict.
"""
import asyncio
from unittest.mock import patch, MagicMock

import pytest

from src.cognition.agent_node import AgentNode
from src.cognition.control_flow import ControlFlowNode
from src.cognition.replanner import ReplanContext
from src.types import (
    EventTriggerSignal,
    NodeRunContext,
    SharedContext,
    TreeExecutionResult,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_ctx() -> SharedContext:
    return SharedContext(lab_id="test", telemetry={}, subsystem_states={}, action_log=[])


def _make_rctx(env=None) -> NodeRunContext:
    if env is None:
        env = _make_simple_env()
    return NodeRunContext(context=_make_ctx(), log=[], max_depth=5, env=env)


class _SimpleEnv:
    """Minimal env stub — no _replanner so inline-replan branch is never triggered."""
    plan_mode = False
    lab_id = "test"


def _make_simple_env() -> _SimpleEnv:
    return _SimpleEnv()


def _stub_node(success: bool) -> AgentNode:
    """Return an AgentNode whose run() always returns a fixed success value."""
    node = AgentNode(node_id="stub", llm_client=None)
    node.goal = "stub_goal"

    async def _run(rctx, step_id, decision_id):
        return TreeExecutionResult(
            success=success, step_id=step_id + 1, decision_id=decision_id + 1
        )

    node.run = _run  # type: ignore[assignment]
    return node


# ------------------------------------------------------------------
# Sequence
# ------------------------------------------------------------------

def test_sequence_all_succeed():
    cf = ControlFlowNode("Sequence")
    cf.children = [_stub_node(True), _stub_node(True)]
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))
    assert result.success is True


def test_sequence_short_circuits_on_first_failure():
    """Second child must NOT be called after the first fails."""
    called = []

    async def run_fail(rctx, s, d):
        called.append("a")
        return TreeExecutionResult(success=False, step_id=s, decision_id=d)

    async def run_ok(rctx, s, d):
        called.append("b")
        return TreeExecutionResult(success=True, step_id=s, decision_id=d)

    node_a = AgentNode(node_id="a", llm_client=None)
    node_a.goal = "a"
    node_a.run = run_fail  # type: ignore[assignment]

    node_b = AgentNode(node_id="b", llm_client=None)
    node_b.goal = "b"
    node_b.run = run_ok  # type: ignore[assignment]

    cf = ControlFlowNode("Sequence")
    cf.children = [node_a, node_b]

    # _SimpleEnv has no _replanner → short-circuit without attempting inline replan
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))

    assert result.success is False
    assert "a" in called
    assert "b" not in called


def test_sequence_empty_children_succeeds():
    cf = ControlFlowNode("Sequence")
    cf.children = []
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))
    assert result.success is True


# ------------------------------------------------------------------
# Fallback
# ------------------------------------------------------------------

def test_fallback_first_success_short_circuits():
    """After the first child succeeds, subsequent children must not run."""
    called = []

    async def run_ok(rctx, s, d):
        called.append("ok")
        return TreeExecutionResult(success=True, step_id=s + 1, decision_id=d + 1)

    async def run_never(rctx, s, d):
        called.append("never")
        return TreeExecutionResult(success=True, step_id=s + 1, decision_id=d + 1)

    node_ok = AgentNode(node_id="ok", llm_client=None)
    node_ok.goal = "ok"
    node_ok.run = run_ok  # type: ignore[assignment]

    node_never = AgentNode(node_id="never", llm_client=None)
    node_never.goal = "never"
    node_never.run = run_never  # type: ignore[assignment]

    cf = ControlFlowNode("Fallback")
    cf.children = [node_ok, node_never]
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))

    assert result.success is True
    assert "never" not in called


def test_fallback_succeeds_on_second_child():
    cf = ControlFlowNode("Fallback")
    cf.children = [_stub_node(False), _stub_node(True)]
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))
    assert result.success is True


def test_fallback_all_fail():
    cf = ControlFlowNode("Fallback")
    cf.children = [_stub_node(False), _stub_node(False)]
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))
    assert result.success is False


# ------------------------------------------------------------------
# Parallel
# ------------------------------------------------------------------

def test_parallel_all_succeed():
    cf = ControlFlowNode("Parallel")
    cf.children = [_stub_node(True), _stub_node(True), _stub_node(True)]
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))
    assert result.success is True


def test_parallel_one_fails():
    cf = ControlFlowNode("Parallel")
    cf.children = [_stub_node(True), _stub_node(False), _stub_node(True)]
    result = asyncio.run(cf.run(_make_rctx(), 1, 1))
    assert result.success is False


def test_parallel_runs_all_children():
    """Parallel must run every child even if one fails."""
    called = []

    def _counting_stub(success: bool, name: str) -> AgentNode:
        node = AgentNode(node_id=name, llm_client=None)
        node.goal = name

        async def _run(rctx, s, d):
            called.append(name)
            return TreeExecutionResult(success=success, step_id=s + 1, decision_id=d + 1)

        node.run = _run  # type: ignore[assignment]
        return node

    cf = ControlFlowNode("Parallel")
    cf.children = [_counting_stub(True, "x"), _counting_stub(False, "y"), _counting_stub(True, "z")]
    asyncio.run(cf.run(_make_rctx(), 1, 1))

    assert set(called) == {"x", "y", "z"}


# ------------------------------------------------------------------
# Control type normalisation
# ------------------------------------------------------------------

def test_lowercase_control_type_normalised():
    cf = ControlFlowNode("sequence")
    assert cf.control_type == "Sequence"


def test_unknown_control_type_falls_back_to_sequence():
    cf = ControlFlowNode("unknown_flow")
    assert cf.control_type == "Sequence"


# ------------------------------------------------------------------
# P0-A regression: inline-replan AgentNode receives env's skill catalog
# ------------------------------------------------------------------

def test_inline_replan_uses_env_skill_catalog():
    """Regression for P0-A: replanned AgentNodes must receive env._mcp skills, not {}."""
    test_skills = {"activate_pump": "start pump", "heat_to_40": "heat sample"}
    captured_kwargs: list = []

    # SpyAgentNode intercepts only the AgentNode constructions that happen
    # inside control_flow._run_children (via the patched module attribute).
    class SpyAgentNode(AgentNode):
        def __init__(self, **kwargs):
            captured_kwargs.append(dict(kwargs))
            super().__init__(**kwargs)

    # Build a mock env that has _replanner and _mcp
    env = MagicMock()
    env.plan_mode = False
    env.lab_id = "test"
    env._mcp.skill_descriptions.return_value = test_skills
    env._agent._llm = None  # force mock planner in replanned nodes

    ctx = _make_ctx()
    env._memory.snapshot.return_value = ctx

    trigger = EventTriggerSignal(source="test", priority=1, preemptive=False)
    replan_result = ReplanContext(
        trigger=trigger,
        failed_step="failing_step",
        context=ctx,
        new_plan=[{"goal": "activate_pump", "skill": "activate_pump", "params": {}}],
        conflict_resolved=True,
    )
    env._replanner.replan.return_value = replan_result

    async def run_fail(rctx, s, d):
        return TreeExecutionResult(success=False, step_id=s, decision_id=d)

    failing_node = MagicMock()
    failing_node.goal = "failing_step"
    failing_node.run = run_fail

    cf = ControlFlowNode("Sequence")
    cf.children = [failing_node]

    rctx = NodeRunContext(context=ctx, log=[], max_depth=5, env=env)

    # Patch AgentNode in its source module so the local import inside
    # control_flow._run_children picks up SpyAgentNode.
    with patch("src.cognition.agent_node.AgentNode", SpyAgentNode):
        asyncio.run(cf.run(rctx, 1, 1))

    assert len(captured_kwargs) > 0, (
        "No AgentNode was constructed during inline replan — "
        "check that the replan branch was triggered"
    )
    for kw in captured_kwargs:
        assert kw.get("available_skills") == test_skills, (
            f"Replanned AgentNode received skills={kw.get('available_skills')!r}; "
            f"expected {test_skills!r}. P0-A fix not applied."
        )
