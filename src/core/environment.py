"""LaboratoryEnvironment — top-level orchestrator (Layer 4/5 bridge).

Wires all layers together and drives the main execution loop:
  1. Ingest task goals
  2. Build telemetry / working memory
  3. Run the hierarchical agent tree (recursive)
  4. Dispatch actions through the output pipeline
  5. Monitor for deviations and trigger replanning
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from src.cognition.agent_node import AgentNode
from src.types import ExecutionResult


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
        max_depth: int = 10,
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
        self._max_depth = max_depth

        self._running = False
        # Flat plan kept for legacy monitor/replanner compatibility
        self._plan: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Main execution loop
    # ------------------------------------------------------------------

    async def run(self, nl_goal: str) -> ExecutionResult:
        """Execute a natural-language mission goal end-to-end using the agent tree."""
        self._running = True
        print(f"[{self.lab_id}] Mission start: {nl_goal}")

        # Start web monitor in background
        asyncio.ensure_future(self._monitor.start())

        # Create root agent node from the injected agent (preserving LLM client)
        root = AgentNode(
            node_id="root",
            llm_client=self._agent._llm if hasattr(self._agent, "_llm") else None,
            depth=0,
        )
        root.goal = nl_goal

        context = self._memory.snapshot()
        log: List[Dict[str, Any]] = []

        print(f"[{self.lab_id}] 🧠 Planner: 开始递归树规划 (max_depth={self._max_depth})")

        # Execute the hierarchical tree
        tree_result = await root.run(
            context=context,
            step_id=1,
            decision_id=1,
            log=log,
            max_depth=self._max_depth,
            env=self,
        )

        self._running = False

        # Build flat plan from log for monitor/output compatibility
        self._plan = [
            entry for entry in log
            if entry.get("type") == "action"
        ]
        tree_text = self._output.format_tree(self._plan)
        await self._monitor.broadcast(self._plan, tree_text)

        # Print summary
        actions = [e for e in log if e.get("type") == "action"]
        expands = [e for e in log if e.get("type") == "expand"]
        print(f"[{self.lab_id}] 规划完成: {len(expands)} 次展开, {len(actions)} 个动作, "
              f"总步数={tree_result.step_id - 1}, success={tree_result.success}")

        status = "completed" if tree_result.success else "failed"
        return ExecutionResult(
            status=status,
            total_steps=tree_result.step_id - 1,
            execution_log=log,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
            replan_ctx = self._replanner.replan(
                trigger=signal,
                failed_step=None,
                context=context,
                remaining_goals=[command.get("goal", "recover")],
            )
            self._plan = replan_ctx.new_plan
