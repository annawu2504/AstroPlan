"""Unit tests for MockScheduler — topological execution and failure injection."""
import asyncio
from unittest.mock import MagicMock

import pytest

from src.evaluation.mock_scheduler import MockScheduler
from src.types import Edge, PlanNode, PlanResponse


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _registry(skill_registered: bool = False) -> MagicMock:
    reg = MagicMock()
    reg.has_skill.return_value = skill_registered
    reg.call.return_value = {"status": "ok"}
    return reg


def _linear_plan(revision: str, skill_names: list) -> PlanResponse:
    """Build a simple linear-dependency PlanResponse."""
    nodes = [
        PlanNode(
            node_id=f"{revision}_n{i + 1}",
            lineage_id=f"lin_{i}",
            skill_name=name,
            depends_on=[f"{revision}_n{i}"] if i > 0 else [],
        )
        for i, name in enumerate(skill_names)
    ]
    edges = [
        Edge(from_id=f"{revision}_n{i + 1}", to_id=f"{revision}_n{i + 2}")
        for i in range(len(nodes) - 1)
    ]
    return PlanResponse(revision_id=revision, nodes=nodes, edges=edges)


def _parallel_plan(revision: str) -> PlanResponse:
    """Plan where b and c both depend on a, but not on each other."""
    a = PlanNode(node_id=f"{revision}_n1", lineage_id="a", skill_name="pre",   depends_on=[])
    b = PlanNode(node_id=f"{revision}_n2", lineage_id="b", skill_name="branch_b", depends_on=[f"{revision}_n1"])
    c = PlanNode(node_id=f"{revision}_n3", lineage_id="c", skill_name="branch_c", depends_on=[f"{revision}_n1"])
    return PlanResponse(
        revision_id=revision,
        nodes=[a, b, c],
        edges=[
            Edge(from_id=f"{revision}_n1", to_id=f"{revision}_n2"),
            Edge(from_id=f"{revision}_n1", to_id=f"{revision}_n3"),
        ],
    )


# ------------------------------------------------------------------
# submit_plan / await_terminal_event
# ------------------------------------------------------------------

def test_all_nodes_complete_when_no_failures():
    sched = MockScheduler(_registry())
    plan = _linear_plan("rev_001", ["a", "b", "c"])

    async def run():
        await sched.submit_plan(plan)
        return await sched.await_terminal_event()

    snap = asyncio.run(run())
    assert snap.all_done is True
    assert len(snap.completed) == 3
    assert len(snap.failed) == 0


def test_topological_execution_order():
    """Skills must be executed in dependency order (a → b → c)."""
    sched = MockScheduler(_registry())
    plan = _linear_plan("rev_001", ["first", "second", "third"])

    async def run():
        await sched.submit_plan(plan)
        await sched.await_terminal_event()

    asyncio.run(run())
    assert sched.executed_skill_names == ["first", "second", "third"]


def test_parallel_nodes_both_execute():
    """Both parallel branches must execute after their shared predecessor."""
    sched = MockScheduler(_registry())
    plan = _parallel_plan("rev_001")

    async def run():
        await sched.submit_plan(plan)
        return await sched.await_terminal_event()

    snap = asyncio.run(run())
    assert snap.all_done is True
    executed = set(sched.executed_skill_names)
    assert "pre" in executed
    assert "branch_b" in executed
    assert "branch_c" in executed


def test_empty_plan_completes_immediately():
    sched = MockScheduler(_registry())
    empty = PlanResponse(revision_id="rev_001", nodes=[], edges=[])

    async def run():
        await sched.submit_plan(empty)
        return await sched.await_terminal_event()

    snap = asyncio.run(run())
    assert snap.all_done is True


# ------------------------------------------------------------------
# Failure injection
# ------------------------------------------------------------------

def test_failure_rate_1_fails_first_node():
    sched = MockScheduler(_registry(), failure_rate=1.0, seed=0)
    plan = _linear_plan("rev_001", ["a", "b", "c"])

    async def run():
        await sched.submit_plan(plan)
        return await sched.await_terminal_event()

    snap = asyncio.run(run())
    assert snap.all_done is False
    assert len(snap.failed) > 0


def test_failure_rate_0_never_fails():
    sched = MockScheduler(_registry(), failure_rate=0.0)
    plan = _linear_plan("rev_001", [f"skill_{i}" for i in range(10)])

    async def run():
        await sched.submit_plan(plan)
        return await sched.await_terminal_event()

    snap = asyncio.run(run())
    assert snap.all_done is True
    assert sched.total_failures == 0


def test_failure_seed_produces_deterministic_results():
    """Same seed must produce identical failure pattern across runs."""

    async def run_once():
        sched = MockScheduler(_registry(), failure_rate=0.3, seed=42)
        plan = _linear_plan("rev_001", [f"s{i}" for i in range(10)])
        await sched.submit_plan(plan)
        snap = await sched.await_terminal_event()
        return snap.all_done, sched.total_failures

    r1 = asyncio.run(run_once())
    r2 = asyncio.run(run_once())
    assert r1 == r2


def test_invalid_failure_rate_raises():
    with pytest.raises(ValueError, match="failure_rate"):
        MockScheduler(_registry(), failure_rate=1.5)


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------

def test_submitted_revisions_tracked():
    sched = MockScheduler(_registry())

    async def run():
        await sched.submit_plan(_linear_plan("rev_001", ["a"]))
        await sched.await_terminal_event()
        await sched.submit_plan(_linear_plan("rev_002", ["b"]))
        await sched.await_terminal_event()

    asyncio.run(run())
    assert "rev_001" in sched.submitted_revisions
    assert "rev_002" in sched.submitted_revisions


def test_total_nodes_executed_cumulative():
    sched = MockScheduler(_registry())

    async def run():
        await sched.submit_plan(_linear_plan("rev_001", ["a", "b"]))
        await sched.await_terminal_event()

    asyncio.run(run())
    assert sched.total_nodes_executed == 2
