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
    AtomicSkillRecord,
    ExecutionNodeRef,
    ExecutionResult,
    MilestoneStateDescription,
    PhysicalConstraints,
    PlanRequest,
    PlanResponse,
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
        self._env: Optional[Any] = None          # LaboratoryEnvironment (lazy)
        self._skill_library: Optional[Any] = None  # SkillLibrary (lazy)

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
        from src.types import NodeRunContext
        root = AgentNode(
            node_id="root",
            llm_client=self._llm,
            depth=0,
            available_skills=self._registry.skill_descriptions(),
            milestone_engine=self._env._milestones,
        )
        root.goal = request.mission_context

        context = mem.snapshot()
        log: list = []
        rctx = NodeRunContext(
            context=context,
            log=log,
            max_depth=self._env._max_depth,
            env=self._env,
        )
        await root.run(rctx, step_id=1, decision_id=1)

        # Safety net: if the LLM produced only Think decisions the DAG is empty.
        # Fall back to the mock planner so benchmarks always get a non-empty plan.
        if self._env._dag.node_count() == 0 and self._llm is not None:
            print(
                f"[AstroPlan] WARNING: LLM produced 0 nodes for {revision_id} "
                f"('{request.mission_context[:60]}'). "
                "Activating mock-planner fallback."
            )
            from src.cognition.agent_node import AgentNode as _AgentNode
            mock_root = _AgentNode(
                node_id="root_mock_fallback",
                llm_client=None,   # None forces mock planner
                depth=0,
                available_skills=self._registry.skill_descriptions(),
                milestone_engine=self._env._milestones,
            )
            mock_root.goal = request.mission_context
            fallback_rctx = NodeRunContext(
                context=mem.snapshot(),
                log=[],
                max_depth=self._env._max_depth,
                env=self._env,
            )
            await mock_root.run(fallback_rctx, step_id=1, decision_id=1)
            print(
                f"[AstroPlan] Mock fallback produced "
                f"{self._env._dag.node_count()} node(s)."
            )

        response = self._env._dag.to_plan_response(revision_id=revision_id)

        print(
            f"[AstroPlan] {revision_id}: {len(response.nodes)} node(s), "
            f"{len(response.edges)} edge(s)"
        )
        await self._reporter.on_plan_generated(response)
        return response

    async def replan(
        self,
        mission: str,
        *,
        failed_node: ExecutionNodeRef,
        current_revision_id: str,
        current_dag: Optional[Any] = None,
        completed_nodes: Optional[List[ExecutionNodeRef]] = None,
    ) -> PlanResponse:
        """Convenience replan triggered by a single node failure.

        Wraps ``plan()`` with a pre-constructed ``PlanRequest`` so callers
        (e.g. agentos_scheduler) don't need to build the request manually.

        Parameters
        ----------
        mission:
            Original mission context string (unchanged across revisions).
        failed_node:
            The ``ExecutionNodeRef`` that failed and needs its sub-tree rebuilt.
        current_revision_id:
            The revision that produced the failing node.
        current_dag:
            The DAG dict from the failing revision (for freeze/diff logic).
        completed_nodes:
            Nodes that completed successfully and should be frozen.
        """
        request = PlanRequest(
            mission_context=mission,
            current_revision_id=current_revision_id,
            current_dag=current_dag,
            completed_nodes=completed_nodes or [],
            failed_nodes=[failed_node],
        )
        return await self.plan(request)

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
            state_before = self._env._memory.snapshot() if self._env else None
            response = await self.plan(request)
            await sched.submit_plan(response)
            snapshot = await sched.await_terminal_event()

            if snapshot.all_done:
                # Record successful execution in SkillLibrary for future retrieval
                self._record_execution(
                    mission=mission,
                    response=response,
                    snapshot_completed=snapshot.completed,
                    state_before=state_before,
                )
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

    def _record_execution(
        self,
        mission: str,
        response: PlanResponse,
        snapshot_completed: list,
        state_before: Optional[Any],
    ) -> None:
        """Record a successful execution in SkillLibrary and refresh MilestoneEngine.

        Converts completed PlanNodes into AtomicSkillRecord objects, builds the
        pre/post MilestoneStateDescription from WorkingMemory snapshots, derives
        PhysicalConstraints from the InterlockEngine FSM states, then calls
        SkillLibrary.observe().  If the library now has newly promoted patterns,
        MilestoneEngine.build_index() is updated immediately so the next plan()
        call benefits from the enriched index.
        """
        if self._skill_library is None or self._env is None:
            return

        # Build AtomicSkillRecord list from the completed PlanNodes in order
        completed_ids = {ref.node_id for ref in snapshot_completed}
        # Preserve topological order from response.nodes
        steps = [
            AtomicSkillRecord(
                skill_name=n.skill_name,
                params=dict(n.params),
                subsystem=self._env._skill_to_subsystem(n.skill_name),
            )
            for n in response.nodes
            if n.node_id in completed_ids
        ]
        if not steps:
            return

        # Pre-state: snapshot taken before plan(); post-state: current memory
        pre_snap = state_before
        post_snap = self._env._memory.snapshot()

        state_before_desc = MilestoneStateDescription(
            subsystem_states=dict(pre_snap.subsystem_states) if pre_snap else {},
            completed_skills=list(pre_snap.action_log[i].get("skill", "")
                                  for i in range(len(pre_snap.action_log)))
                             if pre_snap else [],
            description="state before mission",
        )
        state_after_desc = MilestoneStateDescription(
            subsystem_states=dict(post_snap.subsystem_states),
            completed_skills=[s.skill_name for s in steps],
            description="state after mission",
        )

        # Derive physical constraints from InterlockEngine current FSM states
        try:
            fsm_states = self._interlock.current_states()
        except Exception:
            fsm_states = {}
        constraints = PhysicalConstraints(
            required_preconditions=dict(
                pre_snap.subsystem_states if pre_snap else {}
            ),
            postconditions=fsm_states,
        )

        self._skill_library.observe(
            steps=steps,
            goal_text=mission,
            state_before=state_before_desc,
            state_after=state_after_desc,
            constraints=constraints,
            success=True,
        )

        # Refresh engine only when there are newly promoted patterns
        new_milestones = self._skill_library.export_milestones()
        if new_milestones:
            self._env._milestones.build_index(new_milestones)
            print(
                f"[AstroPlan] MilestoneEngine refreshed: "
                f"{len(new_milestones)} milestone(s) from {self._skill_library.promoted_count()} "
                f"promoted pattern(s) (total observed: {self._skill_library.pattern_count()})"
            )

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
        """Lazy-init LaboratoryEnvironment and SkillLibrary with all sub-components."""
        if self._env is not None:
            return

        from src.memory.working_memory import WorkingMemory
        from src.memory.milestone_engine import MilestoneEngine
        from src.memory.skill_library import SkillLibrary
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
        self._skill_library = SkillLibrary(lab_id=lab_id)
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
