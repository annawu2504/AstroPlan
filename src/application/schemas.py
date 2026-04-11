"""Pydantic v2 API schemas for AstroPlan.

These models mirror src/types.py @dataclass definitions for all types that
cross the HTTP API boundary. FastAPI uses these to auto-generate the OpenAPI
specification, which openapi-typescript then converts to src/types/astroplan.ts.

Rule: only types surfaced in API request/response bodies live here.
Internal planner types (SharedContext, AgentDecision, etc.) are excluded.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NodeStatusEnum(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class MissionStatusEnum(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"


class SseEventTypeEnum(str, Enum):
    PLAN_GENERATED = "plan_generated"
    NODE_STATUS = "node_status"
    REPLAN_TRIGGERED = "replan_triggered"
    HITL_SUSPENDED = "hitl_suspended"
    HITL_RESUMED = "hitl_resumed"
    MISSION_COMPLETED = "mission_completed"
    MISSION_FAILED = "mission_failed"
    COMMAND_QUEUED = "command_queued"
    COMMAND_APPLIED = "command_applied"


# ---------------------------------------------------------------------------
# Plan DAG types
# ---------------------------------------------------------------------------

class EdgeSchema(BaseModel):
    """Directed dependency edge between two plan nodes."""
    from_id: str = Field(..., alias="from", description="Source node_id")
    to_id: str = Field(..., alias="to", description="Target node_id")

    model_config = ConfigDict(populate_by_name=True)


class PlanNodeSchema(BaseModel):
    """One node in a plan DAG as returned by AstroPlan."""
    node_id: str
    lineage_id: str = Field(description="Stable semantic ID; unchanged across replans")
    skill_name: str
    params: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    required_roles: List[str] = Field(default_factory=list)
    tool_hints: List[str] = Field(default_factory=list)
    interruptible: bool = Field(default=True, description="False = requires HITL approval")


class PlanResponseSchema(BaseModel):
    """Complete plan DAG for one revision."""
    revision_id: str
    nodes: List[PlanNodeSchema]
    edges: List[EdgeSchema]


# ---------------------------------------------------------------------------
# HITL gate
# ---------------------------------------------------------------------------

class HITLGateSchema(BaseModel):
    """A suspended execution gate awaiting human approval."""
    gate_id: str
    critical_state: str
    reason: str
    skill_name: str
    params: Dict[str, Any] = Field(default_factory=dict)
    timeout_s: int
    created_at: float = Field(description="Unix epoch seconds")


# ---------------------------------------------------------------------------
# Snapshot (ground truth for SSE reconnection)
# ---------------------------------------------------------------------------

class PlanSnapshotSchema(BaseModel):
    """Complete current state — used by the frontend on SSE reconnect."""
    as_of: int = Field(description="Unix epoch milliseconds of this snapshot")
    revision_id: Optional[str] = None
    nodes: List[PlanNodeSchema] = Field(default_factory=list)
    edges: List[EdgeSchema] = Field(default_factory=list)
    node_statuses: Dict[str, NodeStatusEnum] = Field(default_factory=dict)
    pending_gates: List[HITLGateSchema] = Field(default_factory=list)
    mission_status: MissionStatusEnum = MissionStatusEnum.IDLE
    active_mission: Optional[str] = None
    selected_lab: str = "Fluid-Lab-Demo"
    revisions: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SSE event envelope
# ---------------------------------------------------------------------------

class SseEventSchema(BaseModel):
    """Structured SSE event. Serialised to data: <json>\n\n on the wire."""
    event: SseEventTypeEnum
    revision_id: Optional[str] = None
    timestamp: int = Field(description="Unix epoch milliseconds")
    payload: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# SSE payload subtypes (embedded in SseEventSchema.payload)
# ---------------------------------------------------------------------------

class PlanGeneratedPayload(BaseModel):
    plan: PlanResponseSchema


class NodeStatusPayload(BaseModel):
    node_id: str
    lineage_id: str
    status: NodeStatusEnum


class ReplanTriggeredPayload(BaseModel):
    failed_lineage: str
    old_revision_id: str
    reason: str


class HitlSuspendedPayload(BaseModel):
    gate: HITLGateSchema


class HitlResumedPayload(BaseModel):
    gate_id: str
    approved: bool


class MissionCompletedPayload(BaseModel):
    status: str  # "completed" | "failed"
    total_steps: int
    replan_count: int


# ---------------------------------------------------------------------------
# HTTP request bodies
# ---------------------------------------------------------------------------

class StartMissionRequest(BaseModel):
    mission: str = Field(description="Natural-language mission description")
    lab: str = Field(default="Fluid-Lab-Demo", description="Lab ID from config/labs/")


class HitlRespondRequest(BaseModel):
    gate_id: str
    approved: bool
    updated_constraints: Optional[Dict[str, Any]] = None


class InjectCommandRequest(BaseModel):
    command: str = Field(description="Ground command or feedback text")


# ---------------------------------------------------------------------------
# HTTP response bodies
# ---------------------------------------------------------------------------

class StartMissionResponse(BaseModel):
    ok: bool
    message: str = ""


class HitlRespondResponse(BaseModel):
    ok: bool
    message: str = ""


class InjectCommandResponse(BaseModel):
    ok: bool
    queued: bool = False
    message: str = ""


class HealthResponse(BaseModel):
    status: str = "healthy"
    mission_status: MissionStatusEnum
    pending_gates: int
    revision_id: Optional[str] = None


class LabListResponse(BaseModel):
    labs: List[str]
