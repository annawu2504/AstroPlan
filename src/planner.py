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

Usage — test injection
----------------------
    env = MockLaboratoryEnvironment(...)
    planner = AstroPlan(config, interlock, registry, env=env)
    # _build_components() is skipped; env is used directly.

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

import asyncio
import hashlib
from typing import Any, List, Optional

from src.application.ground_command_receiver import GroundCommandReceiver
from src.application.hitl_operator import HITLSuspensionOperator
from src.application.web_monitor import WebMonitor
from src.cognition.agent_node import AgentNode
from src.cognition.control_flow import ControlFlowNode
from src.cognition.latency_observer import LatencyObserver
from src.cognition.replanner import SubTreeReplanner
from src.control.dag_builder import DAGBuilder
from src.control.output_controller import OutputController
from src.core.config_loader import AppConfig
from src.core.environment import LaboratoryEnvironment
from src.evaluation.mock_scheduler import MockScheduler
from src.execution.hardware_executor import HardwareExecutor
from src.interfaces.scheduler_adapter import ISchedulerAdapter, IStatusReporter
from src.memory.milestone_engine import MilestoneEngine
from src.memory.skill_library import SkillLibrary
from src.memory.working_memory import WorkingMemory
from src.types import (
    AtomicSkillRecord,
    ExecutionNodeRef,
    ExecutionResult,
    MilestoneStateDescription,
    NodeRunContext,
    PhysicalConstraints,
    PlanNode,
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
# Passive telemetry monitor (P2)
# ---------------------------------------------------------------------------

async def _passive_monitor(env: Any, sched: Any, stop: asyncio.Event) -> None:
    """Background coroutine that polls telemetry thresholds during execution.

    If a threshold violation is detected, requests a scheduler abort so that
    execute_standalone() can trigger replanning (passive trigger path).
    """
    while not stop.is_set():
        try:
            snap = env._memory.snapshot().telemetry
            violations = env._interlock.check_thresholds(snap)
            if violations:
                v = violations[0]
                print(
                    f"[AstroPlan] Passive trigger: {v['key']}={v['value']} "
                    f"violates {v['spec']} — requesting abort"
                )
                await sched.request_abort(reason=str(v))
                return
        except Exception:
            pass  # attribute errors during test mocking — keep loop alive
        await asyncio.sleep(1.0)


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
    env:
        Pre-built LaboratoryEnvironment; if provided, _build_components() is
        skipped.  Intended for testing and integration scenarios where the caller
        constructs the environment directly.
    skill_library:
        Pre-built SkillLibrary; used only when ``env`` is also provided.  When
        ``env`` is None this parameter is ignored (the library is built inside
        _build_components).
    """

    def __init__(
        self,
        config: AppConfig,
        interlock: Any,
        registry: Any,
        *,
        llm_client: Optional[Any] = None,
        status_reporter: Optional[Any] = None,
        env: Optional[Any] = None,
        skill_library: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._interlock = interlock
        self._registry = registry
        self._llm = llm_client
        self._reporter: Any = status_reporter or _NullStatusReporter()
        self._revision_counter: int = 0

        # Eager initialisation — configuration errors surface immediately at
        # construction time rather than being deferred to the first plan() call.
        # Tests may inject a pre-built env to bypass _build_components().
        if env is not None:
            self._env: Any = env
            self._skill_library: Any = (
                skill_library or SkillLibrary(lab_id=config.lab_id)
            )
        else:
            self._env, self._skill_library = self._build_components()

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
        revision_id = self._make_revision_id(request.current_revision_id)
        lab_id = self._config.lab_id

        # Fresh DAG for this revision
        self._env._dag = DAGBuilder(revision_id=revision_id, mission_id=lab_id)

        # Activate dry-run mode
        self._env.plan_mode = True

        # Reset working memory; re-seed FSM states
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
            mock_root = AgentNode(
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
        sched = scheduler or MockScheduler(self._registry, failure_rate=0.0)
        eff_reporter = reporter or self._reporter

        max_replans = self._config.orchestrator.max_replan_depth
        request = PlanRequest(mission_context=mission)

        snapshot: Any = None
        for attempt in range(max_replans + 1):
            # Capture the pre-mission world state.  self._env is always non-None
            # (initialised eagerly in __init__) so no conditional is needed here.
            # plan() will reset _env._memory internally; this snapshot reflects
            # the FSM state and any action history from previous attempts.
            state_before = self._env._memory.snapshot()

            response = await self.plan(request)
            await sched.submit_plan(response)
            stop = asyncio.Event()
            monitor_task = asyncio.create_task(
                _passive_monitor(self._env, sched, stop)
            )
            try:
                snapshot = await sched.await_terminal_event()
            finally:
                stop.set()
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass

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

    def _load_manual_milestones(self, lab_id: str, milestone_engine: Any) -> None:
        """Parse ``config/labs/{lab_id}/manual.txt`` and seed MilestoneEngine.

        No-op when the file is absent, the LLM client is None (mock mode),
        or the registry has no registered skills yet.
        """
        import os
        manual_path = os.path.join("config", "labs", lab_id, "manual.txt")
        if not os.path.exists(manual_path):
            return
        if self._llm is None:
            return

        from src.memory.manual_parser import ManualParser
        parser = ManualParser(self._llm, self._registry)
        try:
            with open(manual_path, "r", encoding="utf-8") as fh:
                manual_text = fh.read()
            manual_milestones = parser.parse(manual_text, lab_id)
        except Exception as exc:
            print(f"[AstroPlan] ManualParser error for '{manual_path}': {exc}")
            return

        if manual_milestones:
            milestone_engine.build_index(manual_milestones)
            print(
                f"[AstroPlan] ManualParser: {len(manual_milestones)} milestone(s) "
                f"loaded from '{manual_path}'"
            )

    def _make_revision_id(self, prev: Optional[str]) -> str:
        self._revision_counter += 1
        return f"rev_{self._revision_counter:03d}"

    def _build_components(self) -> tuple:
        """Construct all collaborators. Raises immediately on configuration error.

        Returns
        -------
        (LaboratoryEnvironment, SkillLibrary)
        """
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
        skill_library = SkillLibrary(lab_id=lab_id)

        # Load milestones from experiment manual if available (P4 — ManualParser).
        # Only attempted when an LLM client is configured; mock mode skips silently.
        self._load_manual_milestones(lab_id, milestone_engine)
        gcr = GroundCommandReceiver()
        hitl = HITLSuspensionOperator(
            timeout_s=self._config.orchestrator.hitl_timeout_s
        )
        monitor = WebMonitor(
            host=self._config.web_monitor.host,
            port=self._config.web_monitor.port,
            enabled=self._config.web_monitor.enabled,
        )

        env = LaboratoryEnvironment(
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

        return env, skill_library


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
