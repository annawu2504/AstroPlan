"""Strongly-typed cross-layer dataclasses for AstroPlan.

No bare dict is allowed to cross layer boundaries; use these types instead.
All fields use typing-module generics for Python 3.8+ compatibility.

Type groups
-----------
Primitive identifiers      TransactionID
Observation / memory       SharedContext, Milestone
Planning / cognition       AgentDecision
Events / signals           DeviationEvent, EventTriggerSignal, InterventionSignal, ResumeSignal
Execution                  ExecutionResult
DAG output (internal)      AtomicAction, DAGNode, Edge
Tree execution             NodeRunContext, TreeExecutionResult
Planner ↔ Scheduler API   NodeStatus, PlanNode, ExecutionNodeRef, PlanRequest, PlanResponse
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Primitive identifiers
# ---------------------------------------------------------------------------

@dataclass
class TransactionID:
    """Handle for an async long-running hardware action (e.g. heating)."""
    tx_id: str
    subsystem: str
    issued_at: float  # epoch seconds


# ---------------------------------------------------------------------------
# Observation / memory
# ---------------------------------------------------------------------------

@dataclass
class SharedContext:
    """Global shared observation state — the single source of truth for the agent tree.

    All AgentNode instances read from this object; no node receives a bare dict.
    """
    lab_id: str
    telemetry: Dict[str, Any] = field(default_factory=dict)
    subsystem_states: Dict[str, str] = field(default_factory=dict)
    action_log: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: int = 0  # Unix ms of last update


@dataclass
class Milestone:
    """A retrieved expert trajectory snippet used as a few-shot prompt hint."""
    goal: str
    trajectory: List[Dict[str, Any]] = field(default_factory=list)
    score: float = 0.0  # retrieval relevance score (higher = more relevant)


# ---------------------------------------------------------------------------
# Planning / cognition
# ---------------------------------------------------------------------------

@dataclass
class AgentDecision:
    """Output of AgentNode.execute_decision()."""
    skill: str                      # "Think" | "Act" | "Expand"
    action: Dict[str, Any] = field(default_factory=dict)  # native internal action object
    reasoning: str = ""             # internal chain-of-thought; NOT serialized outward


# ---------------------------------------------------------------------------
# Events / signals
# ---------------------------------------------------------------------------

@dataclass
class DeviationEvent:
    """Raised by TelemetryBus when a sensor reading breaches a safety threshold."""
    sensor_key: str
    value: float
    threshold: float
    severity: str      # "WARNING" | "CRITICAL"
    timestamp: int     # Unix ms


@dataclass
class EventTriggerSignal:
    """Unified trigger that flows from Layer 5 downward to kick off replanning."""
    source: str                         # "ground_command" | "telemetry_deviation" | "hitl"
    priority: int                       # 0 = lowest, 10 = highest
    preemptive: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: int = 0


@dataclass
class InterventionSignal:
    """Human-in-the-loop input arriving from the Web UI."""
    operator_id: str
    approved: bool
    reason: Optional[str] = None
    updated_constraints: Optional[Dict[str, Any]] = None
    timestamp: int = 0


@dataclass
class ResumeSignal:
    """Returned by HITLSuspensionOperator after human review."""
    approved: bool
    updated_constraints: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Final outcome of a mission run."""
    status: str                                      # "completed" | "failed" | "suspended"
    total_steps: int = 0
    execution_log: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DAG output layer
# ---------------------------------------------------------------------------

@dataclass
class AtomicAction:
    """A single indivisible hardware operation extracted from the planning tree."""
    skill: str
    params: Dict[str, Any] = field(default_factory=dict)
    subsystem: str = ""


@dataclass
class DAGNode:
    """One node in the execution DAG, wrapping one AtomicAction."""
    node_id: str
    action: AtomicAction
    status: str = "pending"  # "pending" | "completed" | "failed"


@dataclass
class Edge:
    """Directed dependency edge between two DAG nodes (from_id must complete before to_id)."""
    from_id: str
    to_id: str
    relation: str = "depends_on"


# ---------------------------------------------------------------------------
# Tree execution (hierarchical planning)
# ---------------------------------------------------------------------------

@dataclass
class NodeRunContext:
    """Per-call invariants passed through the recursive agent tree.

    Bundles the arguments that never change between recursive calls
    (context snapshot, log, depth limit, environment reference) so that
    ControlFlowNode.run() and AgentNode.run() carry only two explicit
    scalars — step_id and decision_id — which are genuinely mutable
    and are still returned via TreeExecutionResult.

    Contrast with ReAcTree, which embeds env/cfg on each node at
    construction time.  NodeRunContext preserves node statelessness while
    eliminating argument repetition: adding a new invariant (e.g. a token
    budget) requires one field here, not a change to every call site.
    """
    context: SharedContext           # current world-state snapshot
    log: List[Dict[str, Any]]        # append-only execution log
    max_depth: int                   # hard recursion depth limit
    env: Any                         # LaboratoryEnvironment reference


@dataclass
class TreeExecutionResult:
    """Result returned by a tree node's run() method.

    Tracks execution state through the recursive tree traversal so that
    parent nodes can aggregate child outcomes.
    """
    success: bool
    step_id: int
    decision_id: int
    terminate_reason: Optional[str] = None  # e.g. "max_depth"


# ---------------------------------------------------------------------------
# Planner ↔ Scheduler public API  (POST /planner/plan interface)
# ---------------------------------------------------------------------------

class NodeStatus(str, Enum):
    """Lifecycle state of a plan node as reported by the execution environment."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanNode:
    """One node in a plan DAG returned by AstroPlan.

    Fields beyond skill_name/params/depends_on are consumed by Worker and
    Scheduler; see ALIGNMENT.md §4.3 for full field semantics.
    """
    node_id: str                                      # "rev002_n1" — unique within revision
    lineage_id: str                                   # Stable semantic ID; unchanged across replans
    skill_name: str                                   # MCP skill to invoke
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)   # Predecessor node_ids; [] = frontier
    required_roles: List[str] = field(default_factory=list)  # Worker role constraints
    tool_hints: List[str] = field(default_factory=list)      # Catalog search hints for dispatch
    interruptible: bool = True                        # False → node must run to atomic completion


@dataclass
class ExecutionNodeRef:
    """Pointer to a node's execution state, sent from Scheduler → AstroPlan on replan."""
    node_id: str
    lineage_id: str
    result: Optional[Dict[str, Any]] = None   # Populated for completed nodes
    error: Optional[str] = None               # Populated for failed nodes


@dataclass
class PlanRequest:
    """Request body for POST /planner/plan.

    A fresh-plan request has current_revision_id=None and all node lists empty.
    A replan request carries the current execution snapshot so AstroPlan can
    freeze completed/running nodes and rebuild only the failed subtree.
    """
    mission_context: str
    current_revision_id: Optional[str] = None
    current_dag: Optional[Dict[str, Any]] = None          # Existing DAG JSON (for replan diff)
    completed_nodes: List[ExecutionNodeRef] = field(default_factory=list)
    running_nodes: List[ExecutionNodeRef] = field(default_factory=list)
    failed_nodes: List[ExecutionNodeRef] = field(default_factory=list)
    latest_inputs: List[str] = field(default_factory=list)    # New constraints / goals
    latest_feedback: List[str] = field(default_factory=list)  # Execution quality feedback


@dataclass
class PlanResponse:
    """Complete plan DAG returned by AstroPlan for one revision.

    revision_id increments with every plan() call so the Scheduler can detect
    stale responses in concurrent replan races.
    """
    revision_id: str
    nodes: List[PlanNode]
    edges: List[Edge]

    def validate(self) -> bool:
        """Topological sort (Kahn's algorithm); raises ValueError on cycle."""
        from collections import deque
        node_ids = {n.node_id for n in self.nodes}
        in_degree: Dict[str, int] = {n.node_id: 0 for n in self.nodes}
        adj: Dict[str, List[str]] = {n.node_id: [] for n in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                if dep in node_ids:
                    adj[dep].append(node.node_id)
                    in_degree[node.node_id] += 1
        queue: deque = deque(nid for nid, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            nid = queue.popleft()
            visited += 1
            for succ in adj[nid]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)
        if visited != len(self.nodes):
            raise ValueError(
                f"PlanResponse DAG contains a cycle "
                f"({visited}/{len(self.nodes)} nodes reached in topological sort)"
            )
        return True
