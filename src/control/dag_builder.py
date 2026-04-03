"""DAGBuilder — Layer 3 execution DAG extractor.

Captures atomic actions as they are executed by the agent tree and records
their sequential dependencies.  When the mission completes, the scaffold
nodes (ControlFlowNode, AgentNode wrappers) are discarded; only AtomicAction
DAG nodes and their dependency edges remain.

Usage::

    dag = DAGBuilder()
    dag.register_action(skill="activate_pump", params={}, subsystem="fluid_pump", status="completed")
    dag.register_action(skill="heat_to_40", params={}, subsystem="thermal", status="completed")
    print(dag.to_dict())
    # {'nodes': [...], 'edges': [{'from': 'act_1', 'to': 'act_2', 'relation': 'depends_on'}]}
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.types import AtomicAction, DAGNode, Edge


class DAGBuilder:
    """Builds an execution DAG by registering atomic actions during tree traversal.

    Dependency model
    ----------------
    Each new action node depends on the immediately preceding registered node,
    forming a linear chain that faithfully represents sequential execution.
    Parallel branches chain through the same predecessor (their shared entry
    point), which is a conservative but always-safe dependency representation
    given the current sequential-parallel implementation.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, DAGNode] = {}   # insertion-ordered (Python 3.7+)
        self._edges: List[Edge] = []
        self._counter: int = 0
        self._last_id: Optional[str] = None    # predecessor for next edge

    # ------------------------------------------------------------------
    # Write API (called during tree execution)
    # ------------------------------------------------------------------

    def register_action(
        self,
        skill: str,
        params: Dict,
        subsystem: str,
        status: str = "completed",
    ) -> str:
        """Register one atomic action and wire a dependency edge from the previous node.

        Returns the newly assigned node_id.
        """
        self._counter += 1
        node_id = f"act_{self._counter}"
        action = AtomicAction(skill=skill, params=dict(params), subsystem=subsystem)
        self._nodes[node_id] = DAGNode(node_id=node_id, action=action, status=status)

        if self._last_id is not None:
            self._edges.append(Edge(from_id=self._last_id, to_id=node_id))

        self._last_id = node_id
        return node_id

    # ------------------------------------------------------------------
    # Read API (called by OutputController / WebMonitor)
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

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)
