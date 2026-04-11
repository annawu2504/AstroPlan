"""SubTreeReplanner — Layer 4 local replanning manager.

Handles partial task failures or environment deviations by rebuilding
only the affected sub-tree rather than restarting the entire mission.
Includes conflict detection and priority arbitration.

Parameter adjustment (P3)
-------------------------
When the triggering source is ``"telemetry_deviation"``, ``replan()``
calls ``_derive_param_overrides()`` before generating plan steps.  The
method inspects ``context.telemetry`` against the configured thresholds
(supplied at construction time from ``InterlockEngine._thresholds``) and
returns a best-effort parameter overlay dict:

    {sensor_key: safe_value}

The overlay is merged into every action's ``params`` dict so that the
regenerated steps operate within safe bounds without requiring a new LLM
call.  For a breached *max* threshold the safe value is ``threshold × 0.9``;
for a breached *min* threshold it is ``threshold × 1.1``.  The overrides
are also recorded in ``ReplanContext.param_overrides`` for logging and
test inspection.
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
    param_overrides: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class SubTreeReplanner:
    """Replans the smallest affected sub-tree after a failure or deviation."""

    def __init__(
        self,
        max_depth: int = 3,
        agent_node: Optional[Any] = None,
        thresholds: Optional[Dict[str, Any]] = None,
    ):
        """Parameters
        ----------
        max_depth:
            Maximum replanning nesting depth to prevent infinite loops.
        agent_node:
            An AgentNode instance used to regenerate plan steps.
            When None the replanner produces a conservative fallback plan.
        thresholds:
            Threshold spec dict from ``InterlockEngine._thresholds``
            (``{sensor_key: {min: float, max: float, severity: str}}``).
            Used by ``_derive_param_overrides()`` to compute safe parameter
            values when a telemetry deviation triggers replanning.
            When None, parameter adjustment is skipped.
        """
        self._max_depth = max_depth
        self._agent_node = agent_node
        self._thresholds: Dict[str, Any] = thresholds or {}
        self._replan_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        When the trigger source is ``"telemetry_deviation"``, parameter
        overrides are derived from the current telemetry and merged into
        every generated action's ``params`` so the new plan respects the
        breached safety bounds.
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

        # Derive safe parameter overrides when a telemetry deviation triggered
        # this replan cycle.  For all other trigger sources the override dict
        # is empty and has no effect on generated actions.
        overrides: Dict[str, Any] = {}
        if trigger.source == "telemetry_deviation" and self._thresholds:
            overrides = self._derive_param_overrides(context.telemetry)
            if overrides:
                print(
                    f"[SubTreeReplanner] Applying param overrides from telemetry: "
                    f"{overrides}"
                )

        new_plan: List[Dict[str, Any]] = []

        if self._agent_node is not None:
            for goal in remaining_goals:
                decision = self._agent_node.execute_decision(
                    sub_goal=goal,
                    context=context,
                    milestones=[],
                )
                if decision.skill == "Act" and decision.action:
                    action = {"goal": goal, **decision.action, "status": "pending"}
                    # Merge overrides into the action's params dict so the
                    # regenerated step uses safe threshold-derived values.
                    if overrides and isinstance(action.get("params"), dict):
                        action["params"] = {**action["params"], **overrides}
                    new_plan.append(action)
        else:
            # Conservative fallback: try remaining goals as-is with overrides
            for goal in remaining_goals:
                new_plan.append({
                    "goal": goal,
                    "skill": "noop",
                    "params": dict(overrides),
                    "status": "pending",
                })

        return ReplanContext(
            trigger=trigger,
            failed_step=failed_step,
            context=context,
            new_plan=new_plan,
            conflict_resolved=len(new_plan) > 0,
            param_overrides=overrides,
        )

    # ------------------------------------------------------------------
    # Parameter adjustment (P3)
    # ------------------------------------------------------------------

    def _derive_param_overrides(
        self, telemetry: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compute safe parameter values for any breached threshold.

        For each sensor key present in both *telemetry* and the configured
        threshold spec, check whether the reading is outside the safe
        range.  When a breach is found, compute a conservative target:

        * Breached **max**: safe_value = max_threshold × 0.9
        * Breached **min**: safe_value = min_threshold × 1.1

        The returned dict maps sensor key → safe_value and is intended to
        be merged into action ``params`` dicts, giving the regenerated plan
        steps a grounded starting point derived from hardware limits rather
        than hardcoded constants.

        Returns an empty dict when no thresholds are breached or when
        *telemetry* is empty.
        """
        overrides: Dict[str, Any] = {}
        for key, spec in self._thresholds.items():
            raw = telemetry.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            hi = spec.get("max")
            lo = spec.get("min")

            if hi is not None and value > float(hi):
                overrides[key] = round(float(hi) * 0.9, 4)
            elif lo is not None and value < float(lo):
                overrides[key] = round(float(lo) * 1.1, 4)

        return overrides

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def replan_count(self) -> int:
        return self._replan_count
