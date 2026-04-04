"""AstroPlan — public entry point implementing IPlannerService.

Encapsulates the hierarchical LLM agent tree, DAGBuilder, SubTreeReplanner,
InterlockEngine, and WorkingMemory into a single planner object.

Usage — integrated mode
-----------------------
    config   = load_config("config/config.yaml")
    interlock = InterlockEngine.from_yaml("config/fsm_rules.yaml", config.lab_id)
    registry = MCPRegistry()
    planner  = AstroPlan(config, interlock, registry)

    response = await planner.plan(PlanRequest(mission_context="进行流体实验..."))

Usage — standalone benchmark
-----------------------------
    result = await planner.execute_standalone("进行流体实验...")

    # With synthetic failures:
    from src.evaluation import MockScheduler
    scheduler = MockScheduler(registry, failure_rate=0.3, seed=42)
    result = await planner.execute_standalone("进行流体实验...", scheduler=scheduler)

plan_mode flag
--------------
plan_mode=True  (inside plan())
    AgentNode._execute_action() skips MCP/HW dispatch; only registers
    actions in DAGBuilder.  Returns synthetic True (dry-run).

plan_mode=False (inside execute_standalone())
    Normal execution — MCP skills are dispatched through the registry
    (i.e. the MockScheduler calls registry.call() per node).
"""
from __future__ import annotations

import hashlib
from typing import Any, List, Optional

from src.core.config_loader import AppConfig
from src.interfaces.scheduler_adapter import ISchedulerAdapter, IStatusReporter
from src.types import (
    ExecutionResult,
    PlanRequest,
    PlanResponse,
    SharedContext,
)


# ---------------------------------------------------------------------------
# NullStatusReporter
# ---------------------------------------------------------------------------

class _NullStatusReporter:
    async def on_plan_generated(self, response: PlanResponse) -> None:
        pass

    async def on_replan_triggered(
        self, failed_lineage: str, current_revision_id: str
    ) -> None:
        pass

    async def on_mission_completed(self, result: ExecutionResult) -> None:
        pass


# ---------------------------------------------------------------------------
# AstroPlan
# ---------------------------------------------------------------------------

class AstroPlan:
    """Hierarchical LLM planner with DAG output and partial replanning.

    Satisfies IPlannerService structurally (Protocol — no explicit inheritance
    needed).  Verify with: isinstance(planner, IPlannerService).

    Parameters
    ----------
    config:
        Loaded AppConfig.
    interlock:
        InterlockEngine shared across plan and execute phases.
    registry:
        MCPRegistry used by MockScheduler to dispatch skills.
    llm_client:
        Object with call(prompt: str) -> str.  None → mock planner in AgentNode.
    status_reporter:
        Optional IStatusReporter.  None → no-op.
    """

    def __init__(
        self,
        config: AppConfig,
        interlock: Any,
        registry: Any,
        *,
        llm_client: Optional[Any] = None,
        status_reporter: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._interlock = interlock
        self._registry = registry
        self._llm = llm_client
        self._reporter: Any = status_reporter or _NullStatusReporter()

        self._revision_counter: int = 0
        self._env: Optional[Any] = None   # LaboratoryEnvironment (lazy)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(self, request: PlanRequest) -> PlanResponse:
        """Generate or regenerate a complete plan DAG (plan_mode=True dry-run).

        Fresh plan  (request.current_revision_id is None):
            Runs the full agent tree against the mission context.

        Replan      (current_revision_id set):
            Seeds completed nodes as frozen into the new DAG, logs completed
            skills into WorkingMemory so the agent skips them, then reruns
            the tree for the remaining goal.
        """
        self._init_components()

        from src.control.dag_builder import DAGBuilder

        revision_id = self._make_revision_id(request.current_revision_id)
        lab_id = self._config.lab_id

        # Fresh DAG for this revision
        self._env._dag = DAGBuilder(revision_id=revision_id, mission_id=lab_id)

        # Activate dry-run mode
        self._env.plan_mode = True

        # Reset working memory; re-seed FSM states
        from src.memory.working_memory import WorkingMemory
        mem = WorkingMemory(lab_id=lab_id)
        for subsystem, state in self._interlock.current_states().items():
            mem.update_subsystem_state(subsystem, state)
        self._env._memory = mem

        # For replan: inform the agent tree what has already been done
        # by replaying completed skills into the action log.
        for ref in request.completed_nodes:
            # lineage_id doubles as a stable skill proxy in the action log
            mem.log_action({"skill": ref.lineage_id, "params": {}, "subsystem": ""})

        # Seed frozen (completed) nodes directly into the new DAG so they
        # appear in the PlanResponse with status=completed.
        if request.completed_nodes and request.current_dag:
            prev_nodes_by_lineage = {
                n["lineage_id"]: n
                for n in (request.current_dag.get("nodes") or [])
                if "lineage_id" in n
            }
            for ref in request.completed_nodes:
                raw = prev_nodes_by_lineage.get(ref.lineage_id)
                if raw:
                    from src.types import PlanNode
                    frozen = PlanNode(
                        node_id=raw.get("node_id", ref.node_id),
                        lineage_id=ref.lineage_id,
                        skill_name=raw.get("skill_name", ref.lineage_id),
                        params=raw.get("params", {}),
                        depends_on=raw.get("depends_on", []),
                        required_roles=raw.get("required_roles", []),
                        tool_hints=raw.get("tool_hints", []),
                        interruptible=raw.get("interruptible", True),
                    )
                    self._env._dag.seed_completed_node(frozen)

        # Run the agent tree (plan_mode=True — only writes to DAGBuilder)
        from src.cognition.agent_node import AgentNode
        root = AgentNode(node_id="root", llm_client=self._llm, depth=0)
        root.goal = request.mission_context

        context = mem.snapshot()
        await root.run(
            context=context,
            step_id=1,
            decision_id=1,
            log=[],
            max_depth=self._env._max_depth,
            env=self._env,
        )

        response = self._env._dag.to_plan_response(revision_id=revision_id)

        print(
            f"[AstroPlan] {revision_id}: {len(response.nodes)} node(s), "
            f"{len(response.edges)} edge(s)"
        )
        await self._reporter.on_plan_generated(response)
        return response

    async def execute_standalone(
        self,
        mission: str,
        *,
        scheduler: Optional[ISchedulerAdapter] = None,
        reporter: Optional[IStatusReporter] = None,
    ) -> ExecutionResult:
        """Full plan–execute–replan loop for independent benchmarking.

        Uses MockScheduler if no external scheduler is provided.
        plan_mode remains False during this call so MockScheduler can
        actually invoke skills via the MCPRegistry.
        """
        from src.evaluation.mock_scheduler import MockScheduler

        sched = scheduler or MockScheduler(self._registry, failure_rate=0.0)
        eff_reporter = reporter or self._reporter

        max_replans = self._config.orchestrator.max_replan_depth
        request = PlanRequest(mission_context=mission)

        for attempt in range(max_replans + 1):
            response = await self.plan(request)
            await sched.submit_plan(response)
            snapshot = await sched.await_terminal_event()

            if snapshot.all_done:
                result = ExecutionResult(
                    status="completed",
                    total_steps=len(snapshot.completed),
                    execution_log=[
                        {"node_id": r.node_id, "lineage_id": r.lineage_id,
                         "result": r.result}
                        for r in snapshot.completed
                    ],
                )
                await eff_reporter.on_mission_completed(result)
                return result

            # At least one failure — replan
            for ref in snapshot.failed:
                await eff_reporter.on_replan_triggered(
                    ref.lineage_id, snapshot.revision_id
                )
                print(
                    f"[AstroPlan] Node failed: lineage={ref.lineage_id} "
                    f"error={ref.error}  →  replanning (attempt {attempt + 1})"
                )

            request = PlanRequest(
                mission_context=mission,
                current_revision_id=snapshot.revision_id,
                current_dag=_response_to_dict(response),
                completed_nodes=snapshot.completed,
                running_nodes=snapshot.running,
                failed_nodes=snapshot.failed,
            )

        # Exhausted replan budget
        result = ExecutionResult(
            status="failed",
            total_steps=len(snapshot.completed) if snapshot else 0,
            execution_log=[],
        )
        await eff_reporter.on_mission_completed(result)
        return result

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def make_lineage_id(mission_id: str, semantic_goal: str) -> str:
        """sha256(mission_id + '::' + semantic_goal)[:12] — stable across revisions."""
        raw = f"{mission_id}::{semantic_goal}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_revision_id(self, prev: Optional[str]) -> str:
        self._revision_counter += 1
        return f"rev_{self._revision_counter:03d}"

    def _init_components(self) -> None:
        """Lazy-init LaboratoryEnvironment with all required sub-components."""
        if self._env is not None:
            return

        from src.memory.working_memory import WorkingMemory
        from src.memory.milestone_engine import MilestoneEngine
        from src.control.output_controller import OutputController
        from src.control.dag_builder import DAGBuilder
        from src.cognition.agent_node import AgentNode
        from src.cognition.control_flow import ControlFlowNode
        from src.cognition.replanner import SubTreeReplanner
        from src.cognition.latency_observer import LatencyObserver
        from src.application.ground_command_receiver import GroundCommandReceiver
        from src.application.hitl_operator import HITLSuspensionOperator
        from src.application.web_monitor import WebMonitor
        from src.execution.hardware_executor import HardwareExecutor
        from src.core.environment import LaboratoryEnvironment

        lab_id = self._config.lab_id

        mem = WorkingMemory(lab_id=lab_id)
        for subsystem, state in self._interlock.current_states().items():
            mem.update_subsystem_state(subsystem, state)

        agent_node = AgentNode(node_id="root", llm_client=self._llm)
        cf_node = ControlFlowNode(control_type="Sequence")
        replanner = SubTreeReplanner(
            max_depth=self._config.orchestrator.max_replan_depth,
            agent_node=agent_node,
        )
        latency_obs = LatencyObserver(
            threshold_ms=self._config.orchestrator.latency_threshold_ms
        )
        hw = HardwareExecutor(
            bandwidth_kbps=self._config.bandwidth_kbps,
            lab_id=lab_id,
        )
        output_ctrl = OutputController(compress=self._config.mcp.compress)
        milestone_engine = MilestoneEngine()
        gcr = GroundCommandReceiver()
        hitl = HITLSuspensionOperator(timeout_s=self._config.orchestrator.hitl_timeout_s)
        monitor = WebMonitor(
            host=self._config.web_monitor.host,
            port=self._config.web_monitor.port,
            enabled=False,  # Never start WebSocket server during planning
        )

        self._env = LaboratoryEnvironment(
            lab_id=lab_id,
            interlock_engine=self._interlock,
            working_memory=mem,
            agent_node=agent_node,
            control_flow_node=cf_node,
            replanner=replanner,
            latency_observer=latency_obs,
            hardware_executor=hw,
            output_controller=output_ctrl,
            milestone_engine=milestone_engine,
            ground_cmd_receiver=gcr,
            hitl_operator=hitl,
            web_monitor=monitor,
            mcp_registry=self._registry,
            plan_mode=False,
        )


# ---------------------------------------------------------------------------
# Internal serialisation helper
# ---------------------------------------------------------------------------

def _response_to_dict(response: PlanResponse) -> dict:
    """Convert PlanResponse to a plain dict for embedding in PlanRequest.current_dag."""
    return {
        "revision_id": response.revision_id,
        "nodes": [
            {
                "node_id": n.node_id,
                "lineage_id": n.lineage_id,
                "skill_name": n.skill_name,
                "params": n.params,
                "depends_on": n.depends_on,
                "required_roles": n.required_roles,
                "tool_hints": n.tool_hints,
                "interruptible": n.interruptible,
            }
            for n in response.nodes
        ],
        "edges": [
            {"from": e.from_id, "to": e.to_id, "relation": e.relation}
            for e in response.edges
        ],
    }
