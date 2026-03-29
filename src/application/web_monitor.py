"""WebMonitor — Layer 5 plan tree SSE server.

Exposes a simple Server-Sent Events endpoint so a browser can watch
the plan tree in real time.  Requires `aiohttp` or falls back to a
stdout-only mode when the package is absent.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional


class WebMonitor:
    """Streams plan tree updates over SSE (or stdout when aiohttp is absent)."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765, enabled: bool = False):
        self._host = host
        self._port = port
        self._enabled = enabled
        self._subscribers: List[Any] = []  # aiohttp Response objects
        self._last_tree: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def broadcast(self, plan_steps: List[Dict[str, Any]], tree_text: str) -> None:
        """Send a plan tree update to all connected SSE clients."""
        self._last_tree = tree_text
        print(tree_text)  # always echo to stdout for logging

        if not self._enabled or not self._subscribers:
            return

        data = json.dumps({"steps": plan_steps, "tree": tree_text})
        dead: List[Any] = []
        for resp in list(self._subscribers):
            try:
                await resp.write(f"data: {data}\n\n".encode("utf-8"))
            except Exception:
                dead.append(resp)
        for d in dead:
            self._subscribers.remove(d)

    async def start(self) -> None:
        """Start the SSE HTTP server (requires aiohttp)."""
        if not self._enabled:
            return
        try:
            from aiohttp import web
        except ImportError:
            print("[WebMonitor] aiohttp not installed — SSE server disabled.")
            return

        app = web.Application()
        app.router.add_get("/events", self._sse_handler)
        app.router.add_get("/", self._index_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        print(f"[WebMonitor] SSE server listening on http://{self._host}:{self._port}/")

    # ------------------------------------------------------------------
    # aiohttp handlers
    # ------------------------------------------------------------------

    async def _sse_handler(self, request: Any) -> Any:
        from aiohttp import web
        resp = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await resp.prepare(request)
        self._subscribers.append(resp)
        # Send last known state immediately on connect
        if self._last_tree:
            await resp.write(
                f"data: {json.dumps({'tree': self._last_tree})}\n\n".encode()
            )
        # Keep connection alive until client disconnects
        try:
            while True:
                await asyncio.sleep(15)
                await resp.write(b": ping\n\n")
        except Exception:
            pass
        return resp

    async def _index_handler(self, request: Any) -> Any:
        from aiohttp import web
        html = (
            "<!doctype html><html><body>"
            "<h2>AstroPlan Live Monitor</h2>"
            "<pre id='tree'>Connecting...</pre>"
            "<script>"
            "const es=new EventSource('/events');"
            "es.onmessage=e=>{const d=JSON.parse(e.data);if(d.tree)document.getElementById('tree').textContent=d.tree;};"
            "</script></body></html>"
        )
        return web.Response(content_type="text/html", text=html)
