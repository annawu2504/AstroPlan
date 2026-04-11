"""AstroPlan FastAPI server.

Exposes the planner over HTTP + SSE so the React WebUI can:
  - Submit missions (POST /mission/start)
  - Stream real-time events (GET /events)
  - Respond to HITL gates (POST /hitl/respond)
  - Inject ground commands (POST /command/inject)
  - Fetch the current state snapshot (GET /plan/snapshot)
  - List available labs (GET /labs)

Run
---
    uvicorn src.application.server:app --host 0.0.0.0 --port 8080 --reload

Or via the convenience script::

    python -m src.application.server

The React frontend is served as static files from the dist/ directory
(built with `bun run build` inside astroplan_webui/).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.application.schemas import (
    HealthResponse,
    HitlRespondRequest,
    HitlRespondResponse,
    HITLGateSchema,
    InjectCommandRequest,
    InjectCommandResponse,
    LabListResponse,
    MissionStatusEnum,
    PlanSnapshotSchema,
    StartMissionRequest,
    StartMissionResponse,
)
from src.application.web_monitor import WebMonitor
from src.core.config_loader import load_config
from src.core.mcp_registry import MCPRegistry
from src.physics.interlock_engine import InterlockEngine
from src.planner import AstroPlan
from src.types import ExecutionResult


# ---------------------------------------------------------------------------
# Application state (singleton, lives for the lifetime of the server)
# ---------------------------------------------------------------------------

class _AppState:
    """Holds live mutable state for the current server session."""

    def __init__(self) -> None:
        self.planner: Optional[AstroPlan] = None
        self.monitor: WebMonitor = WebMonitor(enabled=True)
        self.mission_status: MissionStatusEnum = MissionStatusEnum.IDLE
        self.active_mission: Optional[str] = None
        self.selected_lab: str = "Fluid-Lab-Demo"
        self.revision_id: Optional[str] = None
        self.revisions: List[str] = []
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.node_statuses: Dict[str, str] = {}
        self.pending_gates: List[Dict[str, Any]] = []
        self.active_task: Optional[asyncio.Task] = None
        self.command_queue: List[str] = []
        self.replan_count: int = 0
        self.total_steps: int = 0

    def snapshot(self) -> PlanSnapshotSchema:
        from src.application.schemas import EdgeSchema, NodeStatusEnum, PlanNodeSchema
        return PlanSnapshotSchema(
            as_of=int(time.time() * 1000),
            revision_id=self.revision_id,
            nodes=[PlanNodeSchema(**n) for n in self.nodes],
            edges=[EdgeSchema(**{
                "from": e.get("from", e.get("from_id", "")),
                "to": e.get("to", e.get("to_id", "")),
            }) for e in self.edges],
            node_statuses={k: NodeStatusEnum(v) for k, v in self.node_statuses.items()},
            pending_gates=[HITLGateSchema(**g) for g in self.pending_gates],
            mission_status=self.mission_status,
            active_mission=self.active_mission,
            selected_lab=self.selected_lab,
            revisions=list(self.revisions),
        )


_state = _AppState()


# ---------------------------------------------------------------------------
# Planner factory
# ---------------------------------------------------------------------------

def _build_planner(lab_id: str) -> AstroPlan:
    cfg = load_config()
    cfg.lab_id = lab_id
    # Resolve lab-specific paths
    from src.core.config_loader import _resolve_lab_paths
    cfg.fsm_rules_path, cfg.skills_path = _resolve_lab_paths(lab_id)

    interlock = InterlockEngine.from_yaml(cfg.fsm_rules_path, lab_id)
    registry = MCPRegistry()

    # Register demo skills (same as main.py)
    _register_skills(registry, interlock)

    # Inject the shared WebMonitor as status reporter
    planner = AstroPlan(
        cfg,
        interlock,
        registry,
        status_reporter=_state.monitor,
    )
    return planner


def _register_skills(registry: MCPRegistry, interlock: Any) -> None:
    """Register a minimal set of demo skills on the shared registry."""
    try:
        from main import _register_demo_skills  # type: ignore
        _register_demo_skills(registry, interlock)
    except Exception:
        # Fallback: register a no-op skill so the planner has at least one entry
        @registry.mcp_tool
        def noop(params: dict) -> dict:  # type: ignore
            return {"status": "ok"}


# ---------------------------------------------------------------------------
# Mission execution task
# ---------------------------------------------------------------------------

class _WebMonitorStatusReporter:
    """Bridges AstroPlan lifecycle hooks to the WebMonitor + _AppState."""

    def __init__(self, monitor: WebMonitor, state: _AppState) -> None:
        self._monitor = monitor
        self._state = state

    async def on_plan_generated(self, response: Any) -> None:
        # Update state
        self._state.revision_id = response.revision_id
        if response.revision_id not in self._state.revisions:
            self._state.revisions.append(response.revision_id)

        def _n(n: Any) -> Dict[str, Any]:
            return {
                "node_id": n.node_id,
                "lineage_id": n.lineage_id,
                "skill_name": n.skill_name,
                "params": n.params,
                "depends_on": n.depends_on,
                "required_roles": n.required_roles,
                "tool_hints": n.tool_hints,
                "interruptible": n.interruptible,
            }

        self._state.nodes = [_n(n) for n in response.nodes]
        self._state.edges = [
            {"from": e.from_id, "to": e.to_id} for e in response.edges
        ]
        for n in response.nodes:
            if n.node_id not in self._state.node_statuses:
                self._state.node_statuses[n.node_id] = "pending"
        self._state.mission_status = MissionStatusEnum.EXECUTING
        await self._monitor.on_plan_generated(response)

    async def on_replan_triggered(
        self, failed_lineage: str, current_revision_id: str
    ) -> None:
        self._state.replan_count += 1
        self._state.mission_status = MissionStatusEnum.PLANNING
        await self._monitor.on_replan_triggered(failed_lineage, current_revision_id)

    async def on_mission_completed(self, result: Any) -> None:
        self._state.total_steps = result.total_steps
        self._state.mission_status = (
            MissionStatusEnum.COMPLETED
            if result.status == "completed"
            else MissionStatusEnum.FAILED
        )
        await self._monitor.on_mission_completed(result)
        # Drain queued commands now that the mission is done
        await _drain_command_queue()


async def _drain_command_queue() -> None:
    """Submit any queued commands after mission completion."""
    while _state.command_queue:
        cmd = _state.command_queue.pop(0)
        if _state.planner:
            try:
                env = _state.planner._env
                env.inject_ground_command(cmd)
                await _state.monitor.broadcast_raw({
                    "event": "command_applied",
                    "timestamp": int(time.time() * 1000),
                    "payload": {"command": cmd, "status": "applied"},
                })
            except Exception as exc:
                print(f"[Server] Failed to apply queued command: {exc}")


async def _run_mission(mission: str, lab_id: str) -> None:
    """Background task: build planner for lab, run execute_standalone()."""
    _state.mission_status = MissionStatusEnum.PLANNING
    _state.active_mission = mission
    _state.selected_lab = lab_id
    _state.replan_count = 0
    _state.node_statuses = {}
    _state.nodes = []
    _state.edges = []
    _state.revisions = []

    try:
        reporter = _WebMonitorStatusReporter(_state.monitor, _state)
        planner = _build_planner(lab_id)
        # Override the status reporter with our bridge
        planner._reporter = reporter
        # Patch the MockScheduler node_status callback so we get live updates
        _state.planner = planner

        from src.evaluation.mock_scheduler import MockScheduler
        scheduler = MockScheduler(planner._registry)
        # Monkey-patch to emit node_status SSE events
        _patch_scheduler(scheduler)

        result: ExecutionResult = await planner.execute_standalone(
            mission, scheduler=scheduler, reporter=reporter
        )
        await reporter.on_mission_completed(result)
    except asyncio.CancelledError:
        _state.mission_status = MissionStatusEnum.IDLE
        raise
    except Exception as exc:
        print(f"[Server] Mission error: {exc}")
        _state.mission_status = MissionStatusEnum.FAILED
        await _state.monitor.broadcast_mission_completed("failed", 0)
    finally:
        _state.active_task = None


def _patch_scheduler(scheduler: Any) -> None:
    """Monkey-patch MockScheduler to emit node_status SSE events."""
    original_execute = getattr(scheduler, "_execute_node", None)
    if original_execute is None:
        return

    async def patched_execute(node: Any) -> bool:
        rid = _state.revision_id or ""
        await _state.monitor.broadcast_node_status(
            node_id=node.node_id,
            lineage_id=node.lineage_id,
            status="running",
            revision_id=rid,
        )
        _state.node_statuses[node.node_id] = "running"
        result = await original_execute(node)
        final = "completed" if result else "failed"
        _state.node_statuses[node.node_id] = final
        await _state.monitor.broadcast_node_status(
            node_id=node.node_id,
            lineage_id=node.lineage_id,
            status=final,
            revision_id=rid,
        )
        return result

    scheduler._execute_node = patched_execute


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[AstroPlan Server] Starting up…")
    yield
    print("[AstroPlan Server] Shutting down…")
    if _state.active_task and not _state.active_task.done():
        _state.active_task.cancel()
        try:
            await _state.active_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AstroPlan API",
    version="1.0.0",
    description="Space lab task planning agent — REST + SSE interface",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------

@app.get("/events", summary="Server-Sent Events stream")
async def sse_endpoint(request: Request) -> StreamingResponse:
    queue = _state.monitor.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _state.monitor.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Mission endpoints
# ---------------------------------------------------------------------------

@app.post("/mission/start", response_model=StartMissionResponse, summary="Start a new mission")
async def start_mission(body: StartMissionRequest) -> StartMissionResponse:
    if _state.mission_status in (MissionStatusEnum.PLANNING, MissionStatusEnum.EXECUTING):
        raise HTTPException(status_code=409, detail="A mission is already running. Stop it first.")

    if _state.active_task and not _state.active_task.done():
        _state.active_task.cancel()

    _state.active_task = asyncio.create_task(
        _run_mission(body.mission, body.lab)
    )
    return StartMissionResponse(ok=True, message=f"Mission started on lab '{body.lab}'")


@app.post("/mission/stop", response_model=StartMissionResponse, summary="Abort the running mission")
async def stop_mission() -> StartMissionResponse:
    if _state.active_task and not _state.active_task.done():
        _state.active_task.cancel()
        _state.mission_status = MissionStatusEnum.IDLE
        return StartMissionResponse(ok=True, message="Mission aborted.")
    return StartMissionResponse(ok=False, message="No active mission.")


# ---------------------------------------------------------------------------
# HITL endpoints
# ---------------------------------------------------------------------------

@app.post("/hitl/respond", response_model=HitlRespondResponse, summary="Respond to HITL gate")
async def hitl_respond(body: HitlRespondRequest) -> HitlRespondResponse:
    if _state.planner is None:
        raise HTTPException(status_code=503, detail="No active planner session.")

    # Find the gate in the pending list
    gate = next(
        (g for g in _state.pending_gates if g.get("gate_id") == body.gate_id), None
    )
    if gate is None:
        raise HTTPException(status_code=404, detail=f"Gate '{body.gate_id}' not found.")

    try:
        hitl_op = _state.planner._env._hitl
        hitl_op.resume(
            approved=body.approved,
            updated_constraints=body.updated_constraints,
        )
        _state.pending_gates = [g for g in _state.pending_gates if g.get("gate_id") != body.gate_id]
        if _state.mission_status == MissionStatusEnum.SUSPENDED:
            _state.mission_status = MissionStatusEnum.EXECUTING
        await _state.monitor.broadcast_hitl_resumed(body.gate_id, body.approved)
        action = "approved" if body.approved else "rejected"
        return HitlRespondResponse(ok=True, message=f"Gate {action}.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Command injection
# ---------------------------------------------------------------------------

@app.post("/command/inject", response_model=InjectCommandResponse, summary="Inject a ground command")
async def inject_command(body: InjectCommandRequest) -> InjectCommandResponse:
    if _state.mission_status in (MissionStatusEnum.SUSPENDED,):
        # Safe to inject immediately
        if _state.planner:
            try:
                _state.planner._env.inject_ground_command(body.command)
                await _state.monitor.broadcast_raw({
                    "event": "command_applied",
                    "timestamp": int(time.time() * 1000),
                    "payload": {"command": body.command, "status": "applied"},
                })
                return InjectCommandResponse(ok=True, queued=False, message="Command applied.")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

    if _state.mission_status in (MissionStatusEnum.PLANNING, MissionStatusEnum.EXECUTING):
        # Queue for drain after mission completes
        _state.command_queue.append(body.command)
        if len(_state.command_queue) > 10:
            _state.command_queue.pop(0)  # enforce cap
        await _state.monitor.broadcast_raw({
            "event": "command_queued",
            "timestamp": int(time.time() * 1000),
            "payload": {"command": body.command, "position": len(_state.command_queue)},
        })
        return InjectCommandResponse(ok=True, queued=True, message="Command queued.")

    # Idle — start a new mission with this command as context
    _state.active_task = asyncio.create_task(
        _run_mission(body.command, _state.selected_lab)
    )
    return InjectCommandResponse(ok=True, queued=False, message="Command started as new mission.")


# ---------------------------------------------------------------------------
# Snapshot & health
# ---------------------------------------------------------------------------

@app.get("/plan/snapshot", response_model=PlanSnapshotSchema, summary="Current planner state snapshot")
async def plan_snapshot() -> PlanSnapshotSchema:
    return _state.snapshot()


@app.get("/health", response_model=HealthResponse, summary="Backend health check")
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        mission_status=_state.mission_status,
        pending_gates=len(_state.pending_gates),
        revision_id=_state.revision_id,
    )


@app.get("/labs", response_model=LabListResponse, summary="List available labs")
async def list_labs() -> LabListResponse:
    labs_dir = Path("config/labs")
    if labs_dir.exists():
        labs = sorted(
            d.name for d in labs_dir.iterdir() if d.is_dir()
        )
    else:
        labs = ["Fluid-Lab-Demo"]
    return LabListResponse(labs=labs)


# ---------------------------------------------------------------------------
# Serve built frontend (production)
# ---------------------------------------------------------------------------

_DIST = Path(__file__).parent / "astroplan_webui" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="webui")


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.application.server:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        reload_dirs=["src"],
    )
