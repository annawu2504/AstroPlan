"""Strongly-typed cross-layer dataclasses for AstroPlan.

No bare dict is allowed to cross layer boundaries; use these types instead.
All fields use typing-module generics for Python 3.8+ compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
class TreeExecutionResult:
    """Result returned by a tree node's run() method.

    Tracks execution state through the recursive tree traversal so that
    parent nodes can aggregate child outcomes.
    """
    success: bool
    step_id: int
    decision_id: int
    terminate_reason: Optional[str] = None  # e.g. "max_depth"
