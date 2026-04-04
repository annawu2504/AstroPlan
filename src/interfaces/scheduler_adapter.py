"""Scheduler-side interfaces consumed by AstroPlan.

Three protocols are defined here:

ISchedulerAdapter
    What AstroPlan calls on the execution environment.  In integrated mode
    this is a thin adapter over agentos_scheduler's HTTP/gRPC API.  In
    standalone / benchmark mode it is MockScheduler.

IStatusReporter
    Optional monitoring hooks AstroPlan calls during planning.  The default
    implementation is a no-op; WebMonitor and metrics exporters can implement
    this without coupling into the planner core.

ExecutionSnapshot
    Plain dataclass (not a protocol) — the point-in-time execution state that
    the Scheduler hands back to AstroPlan when requesting a replan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol, runtime_checkable

from src.types import ExecutionNodeRef, ExecutionResult, PlanResponse


# ---------------------------------------------------------------------------
# Data carrier (not a protocol)
# ---------------------------------------------------------------------------

@dataclass
class ExecutionSnapshot:
    """Point-in-time execution state returned by ISchedulerAdapter.

    Used to construct a PlanRequest when the Scheduler detects a failure or
    receives new inputs that require replanning.

    revision_id identifies which plan revision this snapshot belongs to, so
    AstroPlan can detect stale snapshots in concurrent replan races.
    """
    revision_id: str
    completed: List[ExecutionNodeRef] = field(default_factory=list)
    running: List[ExecutionNodeRef] = field(default_factory=list)
    failed: List[ExecutionNodeRef] = field(default_factory=list)
    latest_feedback: List[str] = field(default_factory=list)
    all_done: bool = False   # True when every node is completed (terminal state)


# ---------------------------------------------------------------------------
# ISchedulerAdapter
# ---------------------------------------------------------------------------

@runtime_checkable
class ISchedulerAdapter(Protocol):
    """Interface AstroPlan uses to interact with an execution environment.

    Pull model
    ----------
    AstroPlan → submit_plan() → Scheduler starts executing nodes.
    AstroPlan → await_terminal_event() → blocks until failure or completion.
    Scheduler → (raises replan by returning snapshot with failed nodes)

    The Scheduler never pushes to AstroPlan mid-execution; it collects
    failures and hands back a snapshot, then AstroPlan drives the next plan.

    Implementations
    ---------------
    Integrated:  SchedulerHTTPAdapter   wraps POST requests to agentos_scheduler
    Standalone:  MockScheduler          simulates execution for benchmarking
    """

    async def submit_plan(self, response: PlanResponse) -> None:
        """Hand a complete plan DAG to the scheduler for execution.

        The scheduler is responsible for:
        - Converting PlanResponse nodes to its internal DAGTaskGraph format
        - Dispatching frontier nodes to Workers
        - Tracking node lifecycle (pending → running → completed/failed)

        This call is fire-and-forget; the planner does not block waiting for
        completion.  Use await_terminal_event() to receive the outcome.

        Parameters
        ----------
        response:
            The PlanResponse produced by AstroPlan.plan().  The scheduler
            must honour the revision_id for stale-response detection.
        """
        ...

    async def get_execution_snapshot(self, revision_id: str) -> ExecutionSnapshot:
        """Poll the current execution state for a given plan revision.

        Returns immediately with whatever state the scheduler currently holds.
        For blocking until a terminal event, use await_terminal_event().

        Parameters
        ----------
        revision_id:
            Which plan revision to query.  If the scheduler has already moved
            to a newer revision, it should return a snapshot with all_done=True
            and an empty node list (the caller should discard stale results).
        """
        ...

    async def await_terminal_event(self) -> ExecutionSnapshot:
        """Block until the current plan revision reaches a terminal state.

        Terminal states
        ---------------
        - All nodes completed  → snapshot.all_done = True, failed is empty
        - One or more failures → snapshot.all_done = False, snapshot.failed non-empty

        The Scheduler must unblock this coroutine when either condition is met.
        For MockScheduler, this means running all queued nodes and returning
        the resulting snapshot.

        This is the primary replan trigger: execute_standalone() calls this
        after every submit_plan() and inspects the snapshot for failures.
        """
        ...


# ---------------------------------------------------------------------------
# IStatusReporter
# ---------------------------------------------------------------------------

@runtime_checkable
class IStatusReporter(Protocol):
    """Optional monitoring interface AstroPlan calls during planning.

    Decouples observability concerns from planner logic.  AstroPlan accepts
    an optional IStatusReporter and calls these hooks at key lifecycle events.
    Missing hooks are silently skipped (the interface is fully optional).

    Default implementation: NullStatusReporter (no-ops) in src/planner.py.

    Concrete implementations
    ------------------------
    WebMonitor        broadcasts SSE events to the web UI
    MetricsReporter   pushes Prometheus counters / histograms
    """

    async def on_plan_generated(self, response: PlanResponse) -> None:
        """Called once per successful plan() invocation.

        Provides the full PlanResponse so observers can render a DAG view.
        """
        ...

    async def on_replan_triggered(
        self,
        failed_lineage: str,
        current_revision_id: str,
    ) -> None:
        """Called when replanning is initiated due to a node failure.

        Parameters
        ----------
        failed_lineage:
            lineage_id of the node that caused the replan trigger.
        current_revision_id:
            The revision being replaced (the new revision_id is not yet known).
        """
        ...

    async def on_mission_completed(self, result: ExecutionResult) -> None:
        """Called when execute_standalone() finishes (success or failure)."""
        ...
