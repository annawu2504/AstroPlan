"""ControlFlowNode — Layer 4 control flow coordinator.

Aggregates results from child AgentNodes according to a control type:
  Sequence  — all children must succeed
  Fallback  — first success wins
  Parallel  — majority vote
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from src.types import EventTriggerSignal, NodeRunContext, SharedContext, TreeExecutionResult


class ControlFlowNode:
    """Aggregates child results using a behaviour-tree control strategy."""

    SEQUENCE = "Sequence"
    FALLBACK = "Fallback"
    PARALLEL = "Parallel"

    def __init__(self, control_type: str = "Sequence", depth: int = 0):
        if control_type not in (self.SEQUENCE, self.FALLBACK, self.PARALLEL):
            raise ValueError(
                f"Unknown control_type '{control_type}'. "
                f"Must be Sequence, Fallback, or Parallel."
            )
        self.control_type = control_type
        self.depth = depth
        self.children: List[Any] = []

    def evaluate_children(self, children_results: List[bool]) -> bool:
        """Aggregate child success/failure flags and return the node's result."""
        if not children_results:
            return True  # empty subtree trivially succeeds

        if self.control_type == self.SEQUENCE:
            return all(children_results)

        if self.control_type == self.FALLBACK:
            return any(children_results)

        # PARALLEL — majority vote
        return children_results.count(True) > len(children_results) // 2

    async def run(
        self,
        rctx: NodeRunContext,
        step_id: int,
        decision_id: int,
    ) -> TreeExecutionResult:
        """Execute children according to control flow strategy.

        Parameters
        ----------
        rctx:
            Per-call invariants: world-state context, log, max_depth, env.
        step_id:
            Current step counter (incremented for each Act).
        decision_id:
            Current decision counter (incremented for each Think/Act/Expand).

        Returns
        -------
        TreeExecutionResult with success status and updated counters.
        """
        if self.depth > rctx.max_depth:
            return TreeExecutionResult(
                success=False,
                step_id=step_id,
                decision_id=decision_id,
                terminate_reason="max_depth",
            )

        # Propagate control-flow context to DAGBuilder before running children.
        # plan_mode=True: DAGBuilder needs to know the current control type so
        # that register_action() wires parallel fan-outs and fallback skips
        # correctly.  We save the parent context and restore it in `finally`
        # so nested ControlFlowNodes each manage their own scope.
        _dag_snapshot = None
        if getattr(rctx.env, 'plan_mode', False) and hasattr(rctx.env, '_dag'):
            _dag_snapshot = rctx.env._dag.get_context_snapshot()
            if self.control_type == self.PARALLEL:
                # Capture the last registered node as the shared predecessor
                # for all parallel siblings — they all fan out from here.
                rctx.env._dag.set_context("parallel", parallel_predecessor=rctx.env._dag.last_id)
            elif self.control_type == self.FALLBACK:
                rctx.env._dag.set_context("fallback")
            else:  # SEQUENCE
                rctx.env._dag.set_context("sequence")

        try:
            return await self._run_children(rctx, step_id, decision_id)
        finally:
            if _dag_snapshot is not None:
                rctx.env._dag.restore_context_snapshot(_dag_snapshot)

    async def _run_children(
        self,
        rctx: NodeRunContext,
        step_id: int,
        decision_id: int,
    ) -> TreeExecutionResult:
        """Inner dispatch — executes children under the already-set DAG context."""
        env = rctx.env

        if self.control_type == self.SEQUENCE:
            for i, child in enumerate(self.children):
                result = await child.run(rctx, step_id, decision_id)
                step_id, decision_id = result.step_id, result.decision_id
                if not result.success:
                    # Attempt local sub-tree replanning before propagating failure upward
                    if hasattr(env, '_replanner'):
                        remaining_goals = [c.goal for c in self.children[i + 1:]]
                        trigger = EventTriggerSignal(
                            source="action_failure", priority=5, preemptive=False
                        )
                        fresh_ctx = env._memory.snapshot()
                        replan_ctx = env._replanner.replan(
                            trigger=trigger,
                            failed_step=child.goal,
                            context=fresh_ctx,
                            remaining_goals=remaining_goals,
                        )
                        if replan_ctx.conflict_resolved and replan_ctx.new_plan:
                            print(
                                f"[{env.lab_id}] Replanner: {len(replan_ctx.new_plan)} step(s) "
                                f"replanned after '{child.goal}' failed"
                            )
                            # Lazy import to avoid circular dependency
                            from src.cognition.agent_node import AgentNode
                            replan_rctx = NodeRunContext(
                                context=fresh_ctx,
                                log=rctx.log,
                                max_depth=rctx.max_depth,
                                env=env,
                            )
                            for step in replan_ctx.new_plan:
                                rn = AgentNode(
                                    node_id=f"replan_{step.get('goal', '?')}",
                                    llm_client=env._agent._llm if hasattr(env._agent, "_llm") else None,
                                    depth=self.depth + 1,
                                )
                                rn.goal = step.get("skill", step.get("goal", "noop"))
                                r = await rn.run(replan_rctx, step_id, decision_id)
                                step_id, decision_id = r.step_id, r.decision_id
                                if not r.success:
                                    return TreeExecutionResult(
                                        success=False, step_id=step_id, decision_id=decision_id
                                    )
                            return TreeExecutionResult(
                                success=True, step_id=step_id, decision_id=decision_id
                            )
                    return TreeExecutionResult(
                        success=False, step_id=step_id, decision_id=decision_id
                    )
            return TreeExecutionResult(success=True, step_id=step_id, decision_id=decision_id)

        elif self.control_type == self.FALLBACK:
            for child in self.children:
                result = await child.run(rctx, step_id, decision_id)
                step_id, decision_id = result.step_id, result.decision_id
                if result.success:
                    return TreeExecutionResult(
                        success=True, step_id=step_id, decision_id=decision_id
                    )
            return TreeExecutionResult(success=False, step_id=step_id, decision_id=decision_id)

        elif self.control_type == self.PARALLEL:
            # Run children sequentially and collect all results, then apply majority-vote
            # via evaluate_children() — consistent with the spec and testable independently.
            child_results: List[bool] = []
            for child in self.children:
                result = await child.run(rctx, step_id, decision_id)
                step_id, decision_id = result.step_id, result.decision_id
                child_results.append(result.success)
            success = self.evaluate_children(child_results)
            return TreeExecutionResult(success=success, step_id=step_id, decision_id=decision_id)

        return TreeExecutionResult(success=False, step_id=step_id, decision_id=decision_id)
