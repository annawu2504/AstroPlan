"""LaboratoryEnvironment — top-level orchestrator (Layer 4/5 bridge).

Wires all layers together and drives the main execution loop:
  1. Ingest task goals
  2. Build telemetry / working memory
  3. Run the hierarchical agent tree
  4. Dispatch actions through the output pipeline
  5. Monitor for deviations and trigger replanning
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from src.types import (
    AgentDecision,
    EventTriggerSignal,
    ExecutionResult,
    InterventionSignal,
    SharedContext,
)


class LaboratoryEnvironment:
    """Top-level mission orchestrator for AstroPlan.

    Instantiate this class, register MCP skills, then call
    ``asyncio.run(env.run(goal))``.
    """

    def __init__(
        self,
        lab_id: str,
        interlock_engine: Any,
        working_memory: Any,
        agent_node: Any,
        control_flow_node: Any,
        replanner: Any,
        latency_observer: Any,
        hardware_executor: Any,
        output_controller: Any,
        milestone_engine: Any,
        ground_cmd_receiver: Any,
        hitl_operator: Any,
        web_monitor: Any,
        mcp_registry: Any,
    ):
        self.lab_id = lab_id
        self._interlock = interlock_engine
        self._memory = working_memory
        self._agent = agent_node
        self._cf_node = control_flow_node
        self._replanner = replanner
        self._latency = latency_observer
        self._hw = hardware_executor
        self._output = output_controller
        self._milestones = milestone_engine
        self._gcr = ground_cmd_receiver
        self._hitl = hitl_operator
        self._monitor = web_monitor
        self._mcp = mcp_registry

        self._plan: List[Dict[str, Any]] = []
        self._running = False

    # ------------------------------------------------------------------
    # Main execution loop
    # ------------------------------------------------------------------

    async def run(self, nl_goal: str) -> ExecutionResult:
        """Execute a natural-language mission goal end-to-end."""
        self._running = True
        print(f"[{self.lab_id}] Mission start: {nl_goal}")

        # Start web monitor in background
        asyncio.ensure_future(self._monitor.start())

        # Initial planning
        context = self._memory.snapshot()

        # Build full sequential plan via mock/LLM planner
        self._plan = self._expand_to_steps(nl_goal, context)

        print(f"[{self.lab_id}] \U0001f9e0 Planner: 生成 {len(self._plan)} 步计划")
        for i, step in enumerate(self._plan, 1):
            print(f"    步骤 {i}: {{'skill': '{step.get('skill', '?')}', 'params': {step.get('params', {})}}}")

        # Execute plan steps
        log: List[Dict[str, Any]] = []
        for step in self._plan:
            success = await self._execute_step(step, log)
            if not success:
                # Trigger replanning for remaining steps
                remaining_idx = self._plan.index(step)
                remaining_goals = [
                    s.get("description", s.get("skill", ""))
                    for s in self._plan[remaining_idx:]
                ]
                trigger = EventTriggerSignal(
                    source="step_failed",
                    priority=5,
                    payload={"failed_step": step.get("skill")},
                    preemptive=False,
                )
                replan_ctx = self._replanner.replan(
                    trigger=trigger,
                    failed_step=step.get("skill"),
                    context=self._memory.snapshot(),
                    remaining_goals=remaining_goals,
                )
                if replan_ctx.new_plan:
                    # Splice replacement plan in
                    self._plan[remaining_idx:] = replan_ctx.new_plan

        self._running = False
        tree_text = self._output.format_tree(self._plan)
        await self._monitor.broadcast(self._plan, tree_text)

        return ExecutionResult(
            status="completed",
            total_steps=len(log),
            execution_log=log,
        )

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    async def _execute_step(
        self, step: Dict[str, Any], log: List[Dict[str, Any]]
    ) -> bool:
        skill = step.get("skill", "noop")
        params = step.get("params", {})

        # 1. Interlock check
        try:
            self._interlock.validate_action(skill)
        except Exception as exc:
            print(f"[{self.lab_id}] Interlock blocked '{skill}': {exc}")
            step["status"] = "failed"
            return False

        # 2. Transition FSM subsystem (skills handle their own interlock via MCP handlers)
        subsystem = step.get("subsystem", self._skill_to_subsystem(skill))

        # 3. Dispatch via MCP or direct HW
        step["status"] = "running"
        tree_text = self._output.format_tree(self._plan)
        await self._monitor.broadcast(self._plan, tree_text)

        action_payload = {"skill": skill, "params": params, "subsystem": subsystem}
        if self._mcp.has_skill(skill):
            try:
                result = self._mcp.call(skill, params)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                print(f"[{self.lab_id}] MCP skill '{skill}' raised: {exc}")
                step["status"] = "failed"
                return False
        else:
            wire = bytearray(
                json.dumps(action_payload, ensure_ascii=False).encode("utf-8")
            )
            tx = await self._hw.execute_instruction(wire)
            # For async actions, poll until done
            if skill in self._hw.ASYNC_ACTIONS:
                for _ in range(30):
                    await asyncio.sleep(0.1)
                    res = await self._hw.poll_transaction(tx)
                    if res.status == "completed":
                        break

        step["status"] = "completed"
        self._memory.log_action(action_payload)
        log.append({**action_payload, "status": "completed"})
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _expand_to_steps(self, goal: str, context: SharedContext) -> List[Dict[str, Any]]:
        """Ask the agent node for sequential steps by driving the mock planner."""
        steps: List[Dict[str, Any]] = []
        # Let the agent node iterate its internal skill sequence
        for _ in range(10):  # safety cap
            dec = self._agent.execute_decision(
                sub_goal=goal, context=context, milestones=[]
            )
            if dec.skill != "Act" or not dec.action:
                break
            entry = {**dec.action, "status": "pending", "description": goal}
            steps.append(entry)
            # Update mock context to advance internal state
            self._memory.log_action(dec.action)
            context = self._memory.snapshot()
        return steps

    @staticmethod
    def _skill_to_subsystem(skill: str) -> str:
        mapping = {
            "activate_pump": "fluid_pump",
            "heat_to_40": "thermal",
            "cool_down": "thermal",
            "activate_camera": "camera",
            "centrifuge": "centrifuge",
            "pressure_test": "pressure",
        }
        return mapping.get(skill, "unknown")

    # ------------------------------------------------------------------
    # Ground command injection
    # ------------------------------------------------------------------

    async def inject_ground_command(self, command: Dict[str, Any]) -> None:
        """Inject a ground command into the running mission."""
        signal = self._gcr.receive(command, preemptive=command.get("preemptive", False))
        context = self._memory.snapshot()
        preempt, reason = self._latency.should_preempt(
            incoming=signal, current_priority=1
        )
        if preempt:
            print(f"[{self.lab_id}] PREEMPT: {reason}")
            # Trigger replanning from scratch
            replan_ctx = self._replanner.replan(
                trigger=signal,
                failed_step=None,
                context=context,
                remaining_goals=[command.get("goal", "recover")],
            )
            self._plan = replan_ctx.new_plan
