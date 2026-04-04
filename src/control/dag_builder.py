"""DAGBuilder — Layer 3 execution DAG extractor.

Captures atomic actions as they are executed by the agent tree and records
their dependencies.  When the mission completes, scaffold nodes
(ControlFlowNode, AgentNode wrappers) are discarded; only AtomicAction
DAG nodes and their dependency edges remain.

Two operating modes
-------------------
Standalone / legacy (existing behaviour)
    register_action() with no control-flow parameters.  Builds a linear chain
    where each node depends on the immediately preceding node.  Output via
    to_dict() as before.

Plan mode (new — for AstroPlan.plan() → PlanResponse)
    Control-flow context is set per ControlFlowNode via set_context(), or
    passed inline to register_action().  Parallel branches fan out from a
    shared predecessor.  Fallback registers only the first alternative.
    Output via to_plan_response() → PlanResponse with full PlanNode schema.

Usage (legacy)::

    dag = DAGBuilder()
    dag.register_action(skill="activate_pump", params={}, subsystem="fluid_pump")
    dag.register_action(skill="heat_to_40",    params={}, subsystem="thermal")
    print(dag.to_dict())

Usage (plan mode)::

    dag = DAGBuilder(revision_id="rev_001", mission_id="mission_abc")
    dag.set_context("sequence")
    dag.register_action("activate_pump", {}, "fluid_pump",
                        lineage_id="abc123", required_roles=["operator"])
    dag.set_context("parallel", parallel_predecessor="act_1")
    dag.register_action("heat_to_40",    {}, "thermal",
                        lineage_id="def456", required_roles=["operator"])
    dag.register_action("activate_camera", {}, "camera",
                        lineage_id="ghi789", required_roles=["verifier"])
    response = dag.to_plan_response()
    response.validate()
"""
from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional

from src.types import AtomicAction, DAGNode, Edge, PlanNode, PlanResponse


class DAGBuilder:
    """Builds an execution DAG by registering atomic actions during tree traversal.

    Dependency model (sequence)
    ---------------------------
    Each new action depends on the immediately preceding registered node,
    forming a linear chain.

    Dependency model (parallel)
    ---------------------------
    All parallel children share the same predecessor (parallel_predecessor).
    Parallel siblings have no edges between them.  _last_id is NOT updated
    after registering parallel children so subsequent sequence nodes depend
    on the parallel block's shared entry point.

    Dependency model (fallback)
    ---------------------------
    Only the first alternative is registered; _last_id is updated normally.
    Subsequent alternatives are silently dropped (Worker triggers replan on
    failure, which is the actual fallback mechanism).
    """

    def __init__(
        self,
        revision_id: str = "rev_001",
        mission_id: str = "",
    ) -> None:
        self._nodes: Dict[str, DAGNode] = {}   # insertion-ordered (Python 3.7+)
        self._edges: List[Edge] = []
        self._counter: int = 0
        self._last_id: Optional[str] = None    # predecessor for next sequence edge

        # Plan-mode metadata
        self._revision_id: str = revision_id
        self._mission_id: str = mission_id

        # PlanNode extended fields, indexed by node_id
        self._plan_nodes: Dict[str, PlanNode] = {}

        # Active control-flow context (set by set_context or register_action)
        self._context_type: str = "sequence"                # "sequence"|"parallel"|"fallback"
        self._parallel_predecessor: Optional[str] = None    # shared entry for parallel fans
        self._fallback_registered: bool = False             # True after first fallback child

    # ------------------------------------------------------------------
    # Control-flow context API (called by ControlFlowNode.run())
    # ------------------------------------------------------------------

    def set_context(
        self,
        control_type: str,
        parallel_predecessor: Optional[str] = None,
    ) -> None:
        """Notify DAGBuilder of the current ControlFlowNode context.

        Must be called before registering the children of a ControlFlowNode
        so that register_action() can wire edges correctly.

        Parameters
        ----------
        control_type:
            "sequence" | "parallel" | "fallback"
        parallel_predecessor:
            node_id of the shared entry point for a parallel fan-out.
            Required when control_type="parallel"; ignored otherwise.
        """
        self._context_type = control_type
        self._parallel_predecessor = parallel_predecessor
        self._fallback_registered = False

    @property
    def last_id(self) -> Optional[str]:
        """The node_id of the most recently registered sequence node.

        Used by ControlFlowNode.run() to determine the correct
        parallel_predecessor before entering a Parallel block.
        """
        return self._last_id

    def get_context_snapshot(self) -> dict:
        """Return current context state as a dict for save/restore.

        ControlFlowNode.run() saves this before setting a new context and
        restores it after all children have completed, so that nested
        ControlFlowNodes do not corrupt the parent's context.

        Note: _last_id is intentionally excluded — it reflects actual DAG
        state (which node was last registered) and must not be rolled back.
        """
        return {
            "type": self._context_type,
            "parallel_predecessor": self._parallel_predecessor,
            "fallback_registered": self._fallback_registered,
        }

    def restore_context_snapshot(self, snapshot: dict) -> None:
        """Restore context state saved by get_context_snapshot().

        Called by ControlFlowNode.run() in its finally block to reinstate
        the parent node's context after children finish executing.
        """
        self._context_type = snapshot["type"]
        self._parallel_predecessor = snapshot["parallel_predecessor"]
        self._fallback_registered = snapshot["fallback_registered"]

    # ------------------------------------------------------------------
    # Write API (called during tree execution)
    # ------------------------------------------------------------------

    def register_action(
        self,
        skill: str,
        params: Dict,
        subsystem: str,
        status: str = "completed",
        # Control-flow context (plan_mode parameters)
        context_type: Optional[str] = None,
        parallel_predecessor: Optional[str] = None,
        # Plan-mode extended fields
        lineage_id: Optional[str] = None,
        required_roles: Optional[List[str]] = None,
        tool_hints: Optional[List[str]] = None,
        interruptible: bool = True,
    ) -> str:
        """Register one atomic action and wire dependency edges.

        Legacy call signature (no context_type) maintains backward compatibility:
        each node depends on the immediately preceding node (linear chain).

        Extended call (with context_type) respects ControlFlowNode semantics:
        - sequence:  wire from _last_id, update _last_id
        - parallel:  wire from parallel_predecessor (or _parallel_predecessor),
                     do NOT update _last_id (siblings share the same entry)
        - fallback:  register first child only, skip subsequent calls

        Parameters
        ----------
        skill:
            MCP skill name (also used as skill_name in PlanNode).
        params:
            Skill parameters dict.
        subsystem:
            Physical subsystem identifier (used for FSM mapping).
        status:
            Initial node status ("pending" | "completed" for pre-seeded nodes).
        context_type:
            Overrides the context set by set_context() for this single call.
        parallel_predecessor:
            Overrides self._parallel_predecessor for this single call.
        lineage_id:
            Stable semantic ID across revisions.  Auto-generated via
            AstroPlan.make_lineage_id() if None.
        required_roles:
            Worker role constraints for this node.
        tool_hints:
            MCP catalog search hints for dispatch.
        interruptible:
            False → node must complete atomically (no hot-reload interruption).

        Returns
        -------
        The newly assigned node_id string (e.g. "act_3").
        """
        # Resolve effective context
        eff_ctx = context_type or self._context_type
        eff_par = parallel_predecessor or self._parallel_predecessor

        # Fallback: only register the first alternative
        if eff_ctx == "fallback":
            if self._fallback_registered:
                return ""   # silently drop subsequent alternatives
            self._fallback_registered = True

        self._counter += 1
        node_id = f"{self._revision_id}_n{self._counter}"
        action = AtomicAction(skill=skill, params=dict(params), subsystem=subsystem)
        self._nodes[node_id] = DAGNode(node_id=node_id, action=action, status=status)

        # Wire dependency edge
        if eff_ctx == "parallel" and eff_par is not None:
            # Fan-out: depend on shared predecessor, not _last_id
            self._edges.append(Edge(from_id=eff_par, to_id=node_id))
            # Do NOT update _last_id — all parallel siblings share eff_par
        else:
            # Sequence / fallback / legacy: linear chain
            if self._last_id is not None:
                self._edges.append(Edge(from_id=self._last_id, to_id=node_id))
            self._last_id = node_id

        # Store extended PlanNode fields
        self._plan_nodes[node_id] = PlanNode(
            node_id=node_id,
            lineage_id=lineage_id or "",
            skill_name=skill,
            params=dict(params),
            depends_on=[e.from_id for e in self._edges if e.to_id == node_id],
            required_roles=list(required_roles or []),
            tool_hints=list(tool_hints or []),
            interruptible=interruptible,
        )

        return node_id

    def seed_completed_node(self, plan_node: PlanNode) -> None:
        """Inject a completed node from a previous revision (replan: freeze path).

        Used by AstroPlan.plan() to carry completed nodes forward into the new
        DAG without re-executing them.  The node is added with status="completed"
        and its lineage_id is preserved for Worker audit trail.

        Parameters
        ----------
        plan_node:
            PlanNode from the previous PlanResponse, already completed.
        """
        self._nodes[plan_node.node_id] = DAGNode(
            node_id=plan_node.node_id,
            action=AtomicAction(
                skill=plan_node.skill_name,
                params=plan_node.params,
                subsystem="",
            ),
            status="completed",
        )
        self._plan_nodes[plan_node.node_id] = plan_node
        # Re-wire edges from plan_node.depends_on
        for dep_id in plan_node.depends_on:
            edge = Edge(from_id=dep_id, to_id=plan_node.node_id)
            if edge not in self._edges:
                self._edges.append(edge)

    # ------------------------------------------------------------------
    # Read API — legacy (called by OutputController / WebMonitor)
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict:
        """Return a plain-dict representation suitable for JSON serialisation."""
        return {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "skill": n.action.skill,
                    "params": n.action.params,
                    "subsystem": n.action.subsystem,
                    "status": n.status,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {"from": e.from_id, "to": e.to_id, "relation": e.relation}
                for e in self._edges
            ],
        }

    # ------------------------------------------------------------------
    # Read API — plan mode (called by AstroPlan.plan())
    # ------------------------------------------------------------------

    def to_plan_response(
        self,
        revision_id: Optional[str] = None,
        lab_id: str = "",
    ) -> PlanResponse:
        """Serialise accumulated nodes to a validated PlanResponse.

        Resolves depends_on for every PlanNode from the current edge list,
        then calls validate() to detect cycles before returning.
        """
        rev = revision_id or self._revision_id

        # Build per-node incoming-edge lists from self._edges
        node_deps: Dict[str, List[str]] = {nid: [] for nid in self._plan_nodes}
        for edge in self._edges:
            if edge.to_id in node_deps:
                node_deps[edge.to_id].append(edge.from_id)

        nodes: List[PlanNode] = []
        for node_id, pn in self._plan_nodes.items():
            nodes.append(PlanNode(
                node_id=pn.node_id,
                lineage_id=pn.lineage_id,
                skill_name=pn.skill_name,
                params=dict(pn.params),
                depends_on=node_deps.get(node_id, []),
                required_roles=list(pn.required_roles),
                tool_hints=list(pn.tool_hints),
                interruptible=pn.interruptible,
            ))

        response = PlanResponse(
            revision_id=rev,
            nodes=nodes,
            edges=list(self._edges),
        )
        response.validate()
        return response

    def validate(self) -> bool:
        """Topological sort (Kahn's algorithm) on internal node/edge graph.

        Returns True if acyclic; raises ValueError on cycle.
        Delegates to PlanResponse.validate() for the public API; this
        internal variant operates directly on DAGNode dicts.
        """
        in_degree: Dict[str, int] = {nid: 0 for nid in self._nodes}
        adj: Dict[str, List[str]] = {nid: [] for nid in self._nodes}
        for edge in self._edges:
            if edge.from_id in adj and edge.to_id in in_degree:
                adj[edge.from_id].append(edge.to_id)
                in_degree[edge.to_id] += 1
        queue: deque = deque(nid for nid, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            nid = queue.popleft()
            visited += 1
            for succ in adj[nid]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)
        if visited != len(self._nodes):
            raise ValueError(
                f"DAG contains a cycle: {visited}/{len(self._nodes)} nodes reachable"
            )
        return True

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)
