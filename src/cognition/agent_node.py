"""AgentNode — Layer 4 reasoning node.

Drives an LLM (or mock planner) to produce a step-level AgentDecision
from a sub-goal, SharedContext, and retrieved Milestones.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.types import AgentDecision, Milestone, SharedContext


class AgentNode:
    """One node in the hierarchical LLM agent tree.

    Parameters
    ----------
    node_id:
        Unique identifier within the tree (e.g. ``"root"``, ``"thermal_branch"``).
    llm_client:
        Any object with a ``call(prompt: str) -> str`` method.  Pass ``None``
        to fall back to the built-in mock planner.
    """

    def __init__(self, node_id: str, llm_client: Optional[Any] = None):
        self.node_id = node_id
        self._llm = llm_client

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def execute_decision(
        self,
        sub_goal: str,
        context: SharedContext,
        milestones: List[Milestone],
    ) -> AgentDecision:
        """Produce an AgentDecision for the given sub-goal.

        Uses the LLM when available; falls back to a rule-based mock
        that covers the Fluid-Lab-Demo skill set.
        """
        if self._llm is not None:
            return self._llm_plan(sub_goal, context, milestones)
        return self._mock_plan(sub_goal, context)

    # ------------------------------------------------------------------
    # LLM planning path
    # ------------------------------------------------------------------

    def _llm_plan(
        self, sub_goal: str, context: SharedContext, milestones: List[Milestone]
    ) -> AgentDecision:
        prompt = self._build_prompt(sub_goal, context, milestones)
        raw = self._llm.call(prompt)
        return self._parse_llm_response(raw)

    def _build_prompt(
        self,
        sub_goal: str,
        context: SharedContext,
        milestones: List[Milestone],
    ) -> str:
        milestone_txt = ""
        for m in milestones:
            milestone_txt += f"\n- Goal: {m.goal}\n  Steps: {m.trajectory}"

        return (
            f"You are an autonomous space-lab planning agent.\n"
            f"Lab: {context.lab_id}\n"
            f"Current sub-goal: {sub_goal}\n"
            f"Subsystem states: {json.dumps(context.subsystem_states)}\n"
            f"Latest telemetry: {json.dumps(context.telemetry)}\n"
            f"Expert milestones:{milestone_txt or ' none'}\n\n"
            f"Respond with a JSON object with keys:\n"
            f"  skill: one of Think | Act | Expand\n"
            f"  action: dict with 'skill' and 'params' keys\n"
            f"  reasoning: brief chain-of-thought (internal only)\n"
        )

    def _parse_llm_response(self, raw: str) -> AgentDecision:
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            data = json.loads(raw[start:end])
            return AgentDecision(
                skill=data.get("skill", "Act"),
                action=data.get("action", {}),
                reasoning=data.get("reasoning", ""),
            )
        except (ValueError, json.JSONDecodeError):
            # Graceful degradation: treat response as reasoning, no action
            return AgentDecision(skill="Think", reasoning=raw)

    # ------------------------------------------------------------------
    # Mock planning path (no LLM required)
    # ------------------------------------------------------------------

    _SKILL_SEQUENCE = [
        {"skill": "activate_pump", "params": {}},
        {"skill": "heat_to_40", "params": {}},
        {"skill": "activate_camera", "params": {}},
    ]

    def _mock_plan(
        self, sub_goal: str, context: SharedContext
    ) -> AgentDecision:
        """Rule-based fallback planner for Fluid-Lab-Demo."""
        states = context.subsystem_states
        done = set(context.action_log[i]["skill"] for i in range(len(context.action_log)))

        for step in self._SKILL_SEQUENCE:
            if step["skill"] not in done:
                return AgentDecision(
                    skill="Act",
                    action=step,
                    reasoning=f"Mock planner: next pending skill is '{step['skill']}'",
                )

        return AgentDecision(
            skill="Think",
            action={},
            reasoning="All mock skills completed.",
        )
