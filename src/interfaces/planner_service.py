"""IPlannerService — the contract AstroPlan exposes to external orchestrators.

Interaction model (pull)
------------------------
The Scheduler drives the conversation:

    1. Initial plan:
       response = await planner.plan(PlanRequest(mission_context="..."))

    2. Replan after failure:
       response = await planner.plan(PlanRequest(
           mission_context="...",
           current_revision_id="rev_001",
           completed_nodes=[...],
           failed_nodes=[...],
       ))

    3. Standalone benchmark (no external Scheduler):
       result = await planner.execute_standalone("进行流体实验")

AstroPlan is stateless between plan() calls — all execution state is
provided by the caller in PlanRequest.  The planner never calls back into
the Scheduler during a plan() invocation; callbacks are handled by the
optional IStatusReporter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, runtime_checkable

from typing import Protocol

from src.types import ExecutionResult, PlanRequest, PlanResponse

if TYPE_CHECKING:
    from src.interfaces.scheduler_adapter import ISchedulerAdapter, IStatusReporter


@runtime_checkable
class IPlannerService(Protocol):
    """Structural protocol implemented by AstroPlan (src/planner.py).

    Any object that provides plan() and execute_standalone() with the correct
    signatures satisfies this protocol without explicit inheritance, enabling
    easy mocking and alternative implementations in tests.
    """

    async def plan(self, request: PlanRequest) -> PlanResponse:
        """Generate or regenerate a complete plan DAG.

        Behaviour
        ---------
        Fresh plan   (request.current_revision_id is None):
            Run the full agent tree in plan_mode=True (dry-run), accumulate
            atomic actions in DAGBuilder, return PlanResponse(revision_id="rev_001").

        Replan       (request.current_revision_id is not None):
            1. Extract frozen lineage IDs from completed_nodes + running_nodes.
            2. Replay frozen nodes into the new DAG unchanged.
            3. For each failed node: run SubTreeReplanner against the failed
               lineage_id, inject latest_inputs / latest_feedback into
               WorkingMemory before replanning.
            4. Replan any pending (not yet started) nodes if latest_inputs
               or latest_feedback contain new constraints.
            5. Return PlanResponse(revision_id=prev_revision + 1).

        The returned PlanResponse always satisfies validate() (no cycles).

        Parameters
        ----------
        request:
            Full execution snapshot from the Scheduler.  On first call all
            node lists are empty and current_revision_id is None.

        Returns
        -------
        PlanResponse with monotonically increasing revision_id.

        Raises
        ------
        ValueError
            If the generated DAG contains a cycle (should never happen in
            correct implementations).
        """
        ...

    async def execute_standalone(
        self,
        mission: str,
        *,
        scheduler: Optional["ISchedulerAdapter"] = None,
        reporter: Optional["IStatusReporter"] = None,
    ) -> ExecutionResult:
        """Self-contained execution for benchmarking and development.

        Behaviour
        ---------
        Sets plan_mode=False on the internal LaboratoryEnvironment so actions
        are actually dispatched (via MockScheduler or real hardware).

        If scheduler is None, a MockScheduler is created automatically with
        failure_rate=0.0 (deterministic, no synthetic failures).

        The execution loop is:
            1. Call plan() with empty PlanRequest → get PlanResponse rev_001.
            2. Hand PlanResponse to scheduler.submit_plan().
            3. Await scheduler.await_terminal_event() → ExecutionSnapshot.
            4. If snapshot contains failures → call plan(replan_request) → loop.
            5. On all-completed snapshot → return ExecutionResult.

        Parameters
        ----------
        mission:
            Natural-language mission description (same as PlanRequest.mission_context).
        scheduler:
            Optional external ISchedulerAdapter.  Defaults to MockScheduler.
        reporter:
            Optional IStatusReporter for monitoring hooks.

        Returns
        -------
        ExecutionResult with status, step count, and full execution log.
        """
        ...
