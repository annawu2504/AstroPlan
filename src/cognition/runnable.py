"""RunnableNode — structural protocol for tree execution nodes.

Breaks the circular import between agent_node and control_flow by giving
both modules a shared interface to depend on without importing each other.

Before this protocol existed, the import graph contained a cycle:
    agent_node  → control_flow  (deferred, inside AgentNode.run())
    control_flow → agent_node   (deferred, inside ControlFlowNode._run_children())

After introducing RunnableNode:
    agent_node   → control_flow  (module-level, safe)
    control_flow → runnable      (module-level, for children type annotation)
    control_flow → agent_node    (still deferred inside _run_children, for instantiation only)

The deferred instantiation import in control_flow._run_children is acceptable because
both modules are fully loaded before any mission run begins.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.types import NodeRunContext, TreeExecutionResult


@runtime_checkable
class RunnableNode(Protocol):
    """Structural protocol satisfied by AgentNode and ControlFlowNode.

    Any object with a matching ``run`` signature satisfies this protocol
    without explicit inheritance — both AgentNode and ControlFlowNode do
    so naturally.  ControlFlowNode.children uses this type instead of Any,
    enabling static type-checkers to verify the tree structure.
    """

    async def run(
        self,
        rctx: NodeRunContext,
        step_id: int,
        decision_id: int,
    ) -> TreeExecutionResult: ...
