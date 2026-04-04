"""MockScheduler — ISchedulerAdapter for standalone evaluation.

Simulates the agentos_scheduler execution loop locally so AstroPlan can be
benchmarked without a live scheduler or Worker fleet.

Execution model
---------------
1. submit_plan()           stores PlanResponse, resets node state to PENDING.
2. await_terminal_event()  executes nodes in topological order (respects
                           depends_on).  For each ready node:
                           - _maybe_inject_failure() decides if it fails
                           - _execute_node() calls MCPRegistry.call(skill, params)
                           Returns an ExecutionSnapshot as soon as any node
                           fails, or once all nodes complete.

Usage
-----
    # Deterministic (no failures)
    scheduler = MockScheduler(registry)
    result = await planner.execute_standalone("进行流体实验", scheduler=scheduler)

    # 30 % synthetic failure rate, reproducible seed
    scheduler = MockScheduler(registry, failure_rate=0.3, seed=42)
    result = await planner.execute_standalone(mission, scheduler=scheduler)

Metrics
-------
After execution:
    scheduler.submitted_revisions    list of revision_ids submitted
    scheduler.total_nodes_executed   cumulative across all revisions
    scheduler.total_failures         total injected / real failures
"""
from __future__ import annotations

import asyncio
import random
from typing import Dict, List, Optional

from src.interfaces.scheduler_adapter import ExecutionSnapshot
from src.types import ExecutionNodeRef, NodeStatus, PlanNode, PlanResponse


class MockScheduler:
    """Local ISchedulerAdapter for AstroPlan standalone evaluation.

    Satisfies ISchedulerAdapter structurally (Protocol — no explicit
    inheritance required).

    Parameters
    ----------
    registry:
        MCPRegistry whose registered skills are called per DAG node.
    failure_rate:
        Probability [0.0, 1.0] of synthetically failing any single node.
        0.0 = all succeed (default).
    seed:
        RNG seed for reproducible failure patterns across runs.
    """

    def __init__(
        self,
        registry: object,
        failure_rate: float = 0.0,
        seed: Optional[int] = None,
    ) -> None:
        if not (0.0 <= failure_rate <= 1.0):
            raise ValueError(f"failure_rate must be in [0, 1], got {failure_rate}")
        self._registry = registry
        self._failure_rate = failure_rate
        self._rng = random.Random(seed)

        self._current_response: Optional[PlanResponse] = None
        self._node_states: Dict[str, NodeStatus] = {}

        # Public metrics
        self.submitted_revisions: List[str] = []
        self.total_nodes_executed: int = 0
        self.total_failures: int = 0

    # ------------------------------------------------------------------
    # ISchedulerAdapter implementation
    # ------------------------------------------------------------------

    async def submit_plan(self, response: PlanResponse) -> None:
        """Accept a PlanResponse; mark all nodes as PENDING."""
        self._current_response = response
        self._node_states = {node.node_id: NodeStatus.PENDING for node in response.nodes}
        self.submitted_revisions.append(response.revision_id)
        print(
            f"[MockScheduler] Accepted {response.revision_id}: "
            f"{len(response.nodes)} node(s)"
        )

    async def get_execution_snapshot(self, revision_id: str) -> ExecutionSnapshot:
        """Return current execution state; empty snapshot if revision is stale."""
        if (
            self._current_response is None
            or self._current_response.revision_id != revision_id
        ):
            return ExecutionSnapshot(revision_id=revision_id, all_done=True)
        return self._build_snapshot(all_done=False)

    async def await_terminal_event(self) -> ExecutionSnapshot:
        """Run all pending nodes in topological order; return when done or failed.

        Stops and returns immediately when a node fails so AstroPlan can
        trigger replanning with the current execution snapshot.
        """
        if self._current_response is None:
            return ExecutionSnapshot(revision_id="none", all_done=True)

        while True:
            frontier = self._frontier_nodes()
            if not frontier:
                # No more pending nodes reachable — check if all completed
                all_done = all(
                    s == NodeStatus.COMPLETED
                    for s in self._node_states.values()
                )
                return self._build_snapshot(all_done=all_done)

            for node in frontier:
                self._node_states[node.node_id] = NodeStatus.RUNNING

                if self._maybe_inject_failure(node):
                    self._node_states[node.node_id] = NodeStatus.FAILED
                    self.total_failures += 1
                    print(
                        f"[MockScheduler] ✗ {node.skill_name} "
                        f"(lineage={node.lineage_id}) — synthetic failure"
                    )
                    return self._build_snapshot(all_done=False)

                ref = await self._execute_node(node)
                if ref.error:
                    self._node_states[node.node_id] = NodeStatus.FAILED
                    self.total_failures += 1
                    print(
                        f"[MockScheduler] ✗ {node.skill_name} "
                        f"(lineage={node.lineage_id}) — {ref.error}"
                    )
                    return self._build_snapshot(all_done=False)

                self._node_states[node.node_id] = NodeStatus.COMPLETED
                self.total_nodes_executed += 1
                print(
                    f"[MockScheduler] ✓ {node.skill_name} "
                    f"(lineage={node.lineage_id})"
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_node(self, node: PlanNode) -> ExecutionNodeRef:
        """Dispatch one plan node through MCPRegistry."""
        try:
            reg = self._registry
            if hasattr(reg, "has_skill") and reg.has_skill(node.skill_name):
                result = reg.call(node.skill_name, node.params)
                if asyncio.iscoroutine(result):
                    result = await result
                out = result if isinstance(result, dict) else {"status": "ok"}
            else:
                # Skill not registered — treat as no-op success
                out = {"status": "ok", "note": "skill not in registry"}
            return ExecutionNodeRef(
                node_id=node.node_id,
                lineage_id=node.lineage_id,
                result=out,
            )
        except Exception as exc:
            return ExecutionNodeRef(
                node_id=node.node_id,
                lineage_id=node.lineage_id,
                error=str(exc),
            )

    def _maybe_inject_failure(self, node: PlanNode) -> bool:
        """Return True if this node should be synthetically failed."""
        if self._failure_rate <= 0.0:
            return False
        return self._rng.random() < self._failure_rate

    def _frontier_nodes(self) -> List[PlanNode]:
        """Return PENDING nodes whose every dependency is COMPLETED."""
        if self._current_response is None:
            return []
        result: List[PlanNode] = []
        for node in self._current_response.nodes:
            if self._node_states.get(node.node_id) != NodeStatus.PENDING:
                continue
            deps_done = all(
                self._node_states.get(dep) == NodeStatus.COMPLETED
                for dep in node.depends_on
            )
            if deps_done:
                result.append(node)
        return result

    def _build_snapshot(self, all_done: bool = False) -> ExecutionSnapshot:
        """Assemble an ExecutionSnapshot from current node states."""
        completed: List[ExecutionNodeRef] = []
        running: List[ExecutionNodeRef] = []
        failed: List[ExecutionNodeRef] = []

        if self._current_response:
            for node in self._current_response.nodes:
                status = self._node_states.get(node.node_id, NodeStatus.PENDING)
                ref = ExecutionNodeRef(
                    node_id=node.node_id,
                    lineage_id=node.lineage_id,
                )
                if status == NodeStatus.COMPLETED:
                    ref.result = {"status": "ok"}
                    completed.append(ref)
                elif status == NodeStatus.RUNNING:
                    running.append(ref)
                elif status == NodeStatus.FAILED:
                    ref.error = f"mock_failure:{node.skill_name}"
                    failed.append(ref)

        return ExecutionSnapshot(
            revision_id=(
                self._current_response.revision_id
                if self._current_response else "none"
            ),
            completed=completed,
            running=running,
            failed=failed,
            all_done=all_done,
        )
