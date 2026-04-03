"""ControlFlowNode — Layer 4 control flow coordinator.

Aggregates results from child AgentNodes according to a control type:
  Sequence  — all children must succeed
  Fallback  — first success wins
  Parallel  — majority vote
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from src.types import SharedContext, TreeExecutionResult


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
        context: SharedContext,
        step_id: int,
        decision_id: int,
        log: List[Dict[str, Any]],
        max_depth: int,
        env: Any,
    ) -> TreeExecutionResult:
        """Execute children according to control flow strategy.

        Parameters
        ----------
        context:
            Current shared observation state
        step_id:
            Current step counter (incremented for each Act)
        decision_id:
            Current decision counter (incremented for each Think/Act/Expand)
        log:
            Execution log to append events to
        max_depth:
            Maximum recursion depth to prevent infinite expansion
        env:
            Reference to LaboratoryEnvironment for action execution

        Returns
        -------
        TreeExecutionResult with success status and updated counters
        """
        if self.depth > max_depth:
            return TreeExecutionResult(
                success=False,
                step_id=step_id,
                decision_id=decision_id,
                terminate_reason="max_depth",
            )

        if self.control_type == self.SEQUENCE:
            for child in self.children:
                result = await child.run(context, step_id, decision_id, log, max_depth, env)
                step_id, decision_id = result.step_id, result.decision_id
                if not result.success:
                    return TreeExecutionResult(
                        success=False, step_id=step_id, decision_id=decision_id
                    )
            return TreeExecutionResult(success=True, step_id=step_id, decision_id=decision_id)

        elif self.control_type == self.FALLBACK:
            for child in self.children:
                result = await child.run(context, step_id, decision_id, log, max_depth, env)
                step_id, decision_id = result.step_id, result.decision_id
                if result.success:
                    return TreeExecutionResult(
                        success=True, step_id=step_id, decision_id=decision_id
                    )
            return TreeExecutionResult(success=False, step_id=step_id, decision_id=decision_id)

        elif self.control_type == self.PARALLEL:
            is_success = True
            for child in self.children:
                result = await child.run(context, step_id, decision_id, log, max_depth, env)
                step_id, decision_id = result.step_id, result.decision_id
                if not result.success:
                    is_success = False
            return TreeExecutionResult(success=is_success, step_id=step_id, decision_id=decision_id)

        return TreeExecutionResult(success=False, step_id=step_id, decision_id=decision_id)
