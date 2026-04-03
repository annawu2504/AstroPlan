"""AgentNode — Layer 4 reasoning node.

Drives an LLM (or mock planner) to produce a step-level AgentDecision
from a sub-goal, SharedContext, and retrieved Milestones.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from src.types import AgentDecision, Milestone, SharedContext, TreeExecutionResult


class AgentNode:
    """One node in the hierarchical LLM agent tree.

    Parameters
    ----------
    node_id:
        Unique identifier within the tree (e.g. ``"root"``, ``"thermal_branch"``).
    llm_client:
        Any object with a ``call(prompt: str) -> str`` method.  Pass ``None``
        to fall back to the built-in mock planner.
    depth:
        Current depth in the tree (root = 0)
    """

    def __init__(self, node_id: str, llm_client: Optional[Any] = None, depth: int = 0):
        self.node_id = node_id
        self._llm = llm_client
        self.depth = depth
        self.goal: str = ""  # Set by parent when creating child nodes

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
        # Check if goal contains "and" - if so, expand into sequence
        if " and " in sub_goal.lower():
            subgoals = [s.strip() for s in sub_goal.split(" and ")]
            return AgentDecision(
                skill="Expand",
                action={"control_flow": "Sequence", "subgoals": subgoals},
                reasoning=f"Mock planner: decomposing compound goal into {len(subgoals)} sub-goals",
            )

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

    # ------------------------------------------------------------------
    # Recursive execution
    # ------------------------------------------------------------------

    async def run(
        self,
        context: SharedContext,
        step_id: int,
        decision_id: int,
        log: List[Dict[str, Any]],
        max_depth: int,
        env: Any,
    ) -> TreeExecutionResult:
        """Execute this agent node recursively.

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
        # Get decision from planner
        decision = self.execute_decision(
            sub_goal=self.goal if self.goal else "complete mission",
            context=context,
            milestones=[],
        )
        decision_id += 1

        if decision.skill == "Think":
            # Just reasoning, no action
            log.append({
                "type": "think",
                "node_id": self.node_id,
                "reasoning": decision.reasoning,
                "decision_id": decision_id,
            })
            return TreeExecutionResult(success=True, step_id=step_id, decision_id=decision_id)

        elif decision.skill == "Act":
            # Execute atomic action
            success = await self._execute_action(decision.action, env, log)
            step_id += 1
            return TreeExecutionResult(success=success, step_id=step_id, decision_id=decision_id)

        elif decision.skill == "Expand":
            # Create sub-tree and delegate execution
            control_flow = decision.action.get("control_flow", "Sequence")
            subgoals = decision.action.get("subgoals", [])

            log.append({
                "type": "expand",
                "node_id": self.node_id,
                "control_flow": control_flow,
                "subgoals": subgoals,
                "reasoning": decision.reasoning,
                "decision_id": decision_id,
            })

            # Import here to avoid circular dependency
            from src.cognition.control_flow import ControlFlowNode

            cf_node = ControlFlowNode(control_type=control_flow, depth=self.depth + 1)
            for idx, subgoal in enumerate(subgoals):
                child_agent = AgentNode(
                    node_id=f"{self.node_id}_sub{idx}",
                    llm_client=self._llm,
                    depth=self.depth + 2,
                )
                child_agent.goal = subgoal
                cf_node.children.append(child_agent)

            # Delegate to sub-tree
            return await cf_node.run(context, step_id, decision_id, log, max_depth, env)

        # Unknown skill type
        return TreeExecutionResult(success=False, step_id=step_id, decision_id=decision_id)

    async def _execute_action(
        self, action: Dict[str, Any], env: Any, log: List[Dict[str, Any]]
    ) -> bool:
        """Execute a single atomic action via the environment's execution pipeline.

        Parameters
        ----------
        action:
            Action dict with 'skill' and 'params' keys
        env:
            Reference to LaboratoryEnvironment
        log:
            Execution log to append to

        Returns
        -------
        True if action succeeded, False otherwise
        """
        skill = action.get("skill", "noop")
        params = action.get("params", {})

        # Interlock check
        try:
            env._interlock.validate_action(skill)
        except Exception as exc:
            print(f"[{env.lab_id}] Interlock blocked '{skill}': {exc}")
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "failed",
                "error": str(exc),
            })
            return False

        # Dispatch via MCP or direct HW
        action_payload = {
            "skill": skill,
            "params": params,
            "subsystem": env._skill_to_subsystem(skill),
        }

        try:
            if env._mcp.has_skill(skill):
                result = env._mcp.call(skill, params)
                if asyncio.iscoroutine(result):
                    result = await result
            else:
                import json
                wire = bytearray(
                    json.dumps(action_payload, ensure_ascii=False).encode("utf-8")
                )
                tx = await env._hw.execute_instruction(wire)
                # For async actions, poll until done
                if skill in env._hw.ASYNC_ACTIONS:
                    for _ in range(30):
                        await asyncio.sleep(0.1)
                        res = await env._hw.poll_transaction(tx)
                        if res.status == "completed":
                            break

            # Log success
            env._memory.log_action(action_payload)
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "completed",
            })
            return True

        except Exception as exc:
            print(f"[{env.lab_id}] Action '{skill}' raised: {exc}")
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "failed",
                "error": str(exc),
            })
            return False
