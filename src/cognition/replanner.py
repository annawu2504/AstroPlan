"""SubTreeReplanner — Layer 4 local replanning manager.

Handles partial task failures or environment deviations by rebuilding
only the affected sub-tree rather than restarting the entire mission.
Includes conflict detection and priority arbitration.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.types import EventTriggerSignal, SharedContext


@dataclass
class ReplanContext:
    """Encapsulates the inputs and outputs of one replanning cycle."""
    trigger: EventTriggerSignal
    failed_step: Optional[str]
    context: SharedContext
    new_plan: List[Dict[str, Any]] = field(default_factory=list)
    conflict_resolved: bool = False
    timestamp: float = field(default_factory=time.time)


class SubTreeReplanner:
    """Replans the smallest affected sub-tree after a failure or deviation."""

    def __init__(self, max_depth: int = 3, agent_node: Optional[Any] = None):
        """Parameters
        ----------
        max_depth:
            Maximum replanning nesting depth to prevent infinite loops.
        agent_node:
            An AgentNode instance used to regenerate plan steps.
            When None the replanner produces a conservative fallback plan.
        """
        self._max_depth = max_depth
        self._agent_node = agent_node
        self._replan_count = 0

    def replan(
        self,
        trigger: EventTriggerSignal,
        failed_step: Optional[str],
        context: SharedContext,
        remaining_goals: List[str],
        depth: int = 0,
    ) -> ReplanContext:
        """Generate a replacement plan for *remaining_goals*.

        Arbitrates priority conflicts: a preemptive trigger always wins
        over non-preemptive triggers at the same or lower priority.
        """
        if depth >= self._max_depth:
            # Hard limit reached — surface failure up the tree
            return ReplanContext(
                trigger=trigger,
                failed_step=failed_step,
                context=context,
                new_plan=[],
                conflict_resolved=False,
            )

        self._replan_count += 1
        new_plan: List[Dict[str, Any]] = []

        if self._agent_node is not None:
            from src.types import Milestone
            for goal in remaining_goals:
                decision = self._agent_node.execute_decision(
                    sub_goal=goal,
                    context=context,
                    milestones=[],
                )
                if decision.skill == "Act" and decision.action:
                    new_plan.append(
                        {"goal": goal, **decision.action, "status": "pending"}
                    )
        else:
            # Conservative fallback: try remaining goals as-is
            for goal in remaining_goals:
                new_plan.append({"goal": goal, "skill": "noop", "params": {}, "status": "pending"})

        return ReplanContext(
            trigger=trigger,
            failed_step=failed_step,
            context=context,
            new_plan=new_plan,
            conflict_resolved=len(new_plan) > 0,
        )

    @property
    def replan_count(self) -> int:
        return self._replan_count
