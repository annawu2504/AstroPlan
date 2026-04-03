"""AgentNode — Layer 4 reasoning node.

Drives an LLM (or mock planner) to produce a step-level AgentDecision
from a sub-goal, SharedContext, and retrieved Milestones.
"""
from __future__ import annotations

import asyncio
import json
import time
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
        """Rule-based fallback planner for Fluid-Lab-Demo.

        Decision logic (in priority order):
        1. If sub_goal is an exact atomic skill name → Act immediately.
        2. If sub_goal contains " and " → Expand by splitting on " and ".
        3. Otherwise treat as a high-level goal → Expand into remaining skills
           of _SKILL_SEQUENCE in order (scaffold-stripping: tree nodes are
           scaffolding, only the leaf Act nodes become DAG nodes).
        """
        known = {step["skill"]: step for step in self._SKILL_SEQUENCE}

        # Case 1: goal IS an atomic skill name — execute it directly
        if sub_goal in known:
            return AgentDecision(
                skill="Act",
                action=known[sub_goal],
                reasoning=f"Mock planner: executing atomic skill '{sub_goal}'",
            )

        # Case 2: English compound goal split on " and "
        if " and " in sub_goal.lower():
            subgoals = [s.strip() for s in sub_goal.split(" and ")]
            return AgentDecision(
                skill="Expand",
                action={"control_flow": "Sequence", "subgoals": subgoals},
                reasoning=f"Mock planner: decomposing compound goal into {len(subgoals)} sub-goals",
            )

        # Case 3: high-level goal — expand into all remaining skills in order
        done = {e["skill"] for e in context.action_log}
        remaining = [s for s in known if s not in done]
        if remaining:
            return AgentDecision(
                skill="Expand",
                action={"control_flow": "Sequence", "subgoals": remaining},
                reasoning=f"Mock planner: expanding high-level goal into {len(remaining)} skill sub-goals",
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
        if self.depth > max_depth:
            return TreeExecutionResult(
                success=False,
                step_id=step_id,
                decision_id=decision_id,
                terminate_reason="max_depth",
            )

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

        _t0 = time.time()
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

            # Record RTT for latency estimation (command dispatch → acknowledgement)
            env._latency.record_rtt((time.time() - _t0) * 1000)

            # Log success
            env._memory.log_action(action_payload)
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "completed",
            })
            # Register completed atomic action in the DAG
            env._dag.register_action(
                skill=skill,
                params=params,
                subsystem=env._skill_to_subsystem(skill),
                status="completed",
            )
            return True

        except Exception as exc:
            # Record RTT even on failure so the observer sees the attempt
            env._latency.record_rtt((time.time() - _t0) * 1000)
            print(f"[{env.lab_id}] Action '{skill}' raised: {exc}")
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "failed",
                "error": str(exc),
            })
            # Register failed atomic action in the DAG so the full picture is preserved
            env._dag.register_action(
                skill=skill,
                params=params,
                subsystem=env._skill_to_subsystem(skill),
                status="failed",
            )
            return False
