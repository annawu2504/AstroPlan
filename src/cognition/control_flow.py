"""ControlFlowNode — Layer 4 control flow coordinator.

Aggregates results from child AgentNodes according to a control type:
  Sequence  — all children must succeed
  Fallback  — first success wins
  Parallel  — majority vote
"""
from __future__ import annotations

from typing import List


class ControlFlowNode:
    """Aggregates child results using a behaviour-tree control strategy."""

    SEQUENCE = "Sequence"
    FALLBACK = "Fallback"
    PARALLEL = "Parallel"

    def __init__(self, control_type: str = "Sequence"):
        if control_type not in (self.SEQUENCE, self.FALLBACK, self.PARALLEL):
            raise ValueError(
                f"Unknown control_type '{control_type}'. "
                f"Must be Sequence, Fallback, or Parallel."
            )
        self.control_type = control_type

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
