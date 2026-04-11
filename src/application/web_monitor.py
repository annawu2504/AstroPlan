"""WebMonitor — queue-based SSE broadcaster (framework-agnostic).

Replaces the previous aiohttp-specific implementation with a pure asyncio
queue model. Any HTTP framework (FastAPI, aiohttp, etc.) can subscribe to
the broadcast stream by calling subscribe() to obtain an asyncio.Queue.

Structured event format
-----------------------
Every broadcast carries a typed SseEventSchema JSON object so the frontend
can route events to the correct Zustand store action without string parsing.

Backward compatibility
----------------------
The raw ASCII tree_text is still included in plan_generated payloads as the
"tree" field so that the existing stdout monitor and any legacy clients continue
to work unchanged.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ts() -> int:
    """Current time as Unix epoch milliseconds."""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# WebMonitor
# ---------------------------------------------------------------------------

class WebMonitor:
    """Streams structured SSE events to all registered queue subscribers.

    Usage (FastAPI)::

        monitor = WebMonitor(enabled=True)

        @app.get("/events")
        async def sse(request: Request):
            queue = monitor.subscribe()
            async def gen():
                try:
                    while True:
                        data = await queue.get()
                        yield f"data: {data}\\n\\n"
                except asyncio.CancelledError:
                    pass
                finally:
                    monitor.unsubscribe(queue)
            return StreamingResponse(gen(), media_type="text/event-stream")
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        enabled: bool = False,
    ):
        self._host = host
        self._port = port
        self._enabled = enabled
        self._queues: List[asyncio.Queue] = []
        self._last_snapshot: Optional[str] = None  # last plan_generated event JSON

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber. Returns a queue to read from."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.append(q)
        # Immediately replay the last snapshot so a new client gets current state
        if self._last_snapshot:
            try:
                q.put_nowait(self._last_snapshot)
            except asyncio.QueueFull:
                pass
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue (call in finally block of SSE handler)."""
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    # ------------------------------------------------------------------
    # Broadcast helpers (typed events)
    # ------------------------------------------------------------------

    async def broadcast_raw(self, event: Dict[str, Any]) -> None:
        """Broadcast an arbitrary dict as a structured SSE event."""
        if not self._enabled:
            return
        data = json.dumps(event, ensure_ascii=False)
        dead: List[asyncio.Queue] = []
        for q in list(self._queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    async def broadcast_plan_generated(
        self,
        plan_response: Any,
        tree_text: str = "",
    ) -> None:
        """Emit plan_generated event with full DAG + ASCII tree."""
        from src.application.schemas import SseEventTypeEnum

        def _node(n: Any) -> Dict[str, Any]:
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

        def _edge(e: Any) -> Dict[str, Any]:
            return {"from": e.from_id, "to": e.to_id}

        plan_dict = {
            "revision_id": plan_response.revision_id,
            "nodes": [_node(n) for n in plan_response.nodes],
            "edges": [_edge(e) for e in plan_response.edges],
        }
        event = {
            "event": SseEventTypeEnum.PLAN_GENERATED.value,
            "revision_id": plan_response.revision_id,
            "timestamp": _ts(),
            "payload": {"plan": plan_dict, "tree": tree_text},
        }
        # Cache for late-joining clients
        self._last_snapshot = json.dumps(event, ensure_ascii=False)
        print(tree_text or f"[WebMonitor] plan_generated {plan_response.revision_id}")
        await self.broadcast_raw(event)

    async def broadcast_node_status(
        self,
        node_id: str,
        lineage_id: str,
        status: str,
        revision_id: str = "",
    ) -> None:
        from src.application.schemas import SseEventTypeEnum

        event = {
            "event": SseEventTypeEnum.NODE_STATUS.value,
            "revision_id": revision_id,
            "timestamp": _ts(),
            "payload": {
                "node_id": node_id,
                "lineage_id": lineage_id,
                "status": status,
            },
        }
        await self.broadcast_raw(event)

    async def broadcast_replan_triggered(
        self,
        failed_lineage: str,
        old_revision_id: str,
        reason: str = "",
    ) -> None:
        from src.application.schemas import SseEventTypeEnum

        event = {
            "event": SseEventTypeEnum.REPLAN_TRIGGERED.value,
            "revision_id": old_revision_id,
            "timestamp": _ts(),
            "payload": {
                "failed_lineage": failed_lineage,
                "old_revision_id": old_revision_id,
                "reason": reason,
            },
        }
        print(f"[WebMonitor] replan_triggered lineage={failed_lineage}")
        await self.broadcast_raw(event)

    async def broadcast_hitl_suspended(self, gate: Dict[str, Any]) -> None:
        from src.application.schemas import SseEventTypeEnum

        event = {
            "event": SseEventTypeEnum.HITL_SUSPENDED.value,
            "timestamp": _ts(),
            "payload": {"gate": gate},
        }
        print(f"[WebMonitor] hitl_suspended gate_id={gate.get('gate_id')}")
        await self.broadcast_raw(event)

    async def broadcast_hitl_resumed(self, gate_id: str, approved: bool) -> None:
        from src.application.schemas import SseEventTypeEnum

        event = {
            "event": SseEventTypeEnum.HITL_RESUMED.value,
            "timestamp": _ts(),
            "payload": {"gate_id": gate_id, "approved": approved},
        }
        await self.broadcast_raw(event)

    async def broadcast_mission_completed(
        self,
        status: str,
        total_steps: int,
        replan_count: int = 0,
    ) -> None:
        from src.application.schemas import SseEventTypeEnum

        event_type = (
            SseEventTypeEnum.MISSION_COMPLETED.value
            if status == "completed"
            else SseEventTypeEnum.MISSION_FAILED.value
        )
        event = {
            "event": event_type,
            "timestamp": _ts(),
            "payload": {
                "status": status,
                "total_steps": total_steps,
                "replan_count": replan_count,
            },
        }
        print(f"[WebMonitor] mission_{status} steps={total_steps}")
        await self.broadcast_raw(event)

    # ------------------------------------------------------------------
    # IStatusReporter compatibility hooks
    # ------------------------------------------------------------------

    async def on_plan_generated(self, response: Any) -> None:
        """Called by AstroPlan planner at plan_generated lifecycle point."""
        from src.control.output_controller import OutputController
        try:
            oc = OutputController(compress=False)
            tree_text = oc.format_tree(response)
        except Exception:
            tree_text = f"[plan {response.revision_id}]"
        await self.broadcast_plan_generated(response, tree_text)

    async def on_replan_triggered(
        self, failed_lineage: str, current_revision_id: str
    ) -> None:
        """Called by AstroPlan planner when a replan is triggered."""
        await self.broadcast_replan_triggered(
            failed_lineage=failed_lineage,
            old_revision_id=current_revision_id,
        )

    async def on_mission_completed(self, result: Any) -> None:
        """Called by AstroPlan planner when execute_standalone() finishes."""
        await self.broadcast_mission_completed(
            status=result.status,
            total_steps=result.total_steps,
        )

    # ------------------------------------------------------------------
    # Legacy / stdout path (no longer starts an aiohttp server)
    # ------------------------------------------------------------------

    async def broadcast(self, plan_steps: list, tree_text: str) -> None:
        """Legacy broadcast compat — proxies to broadcast_raw with tree payload."""
        import json as _json
        data: Dict[str, Any] = {"steps": plan_steps, "tree": tree_text}
        print(tree_text)
        await self.broadcast_raw({"event": "legacy_tree", "timestamp": _ts(), "payload": data})

    async def start(self) -> None:
        """No-op: SSE is now served by FastAPI in server.py."""
        if self._enabled:
            print("[WebMonitor] SSE broadcasting via FastAPI /events endpoint.")
