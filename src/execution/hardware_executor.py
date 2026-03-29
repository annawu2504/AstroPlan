"""HardwareExecutor — Layer 1 software/hardware execution interface.

Receives serialized instruction bytes from OutputController and dispatches
them to subsystems.  Long-running actions return a TransactionID instead
of blocking; callers poll with poll_transaction().
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, Optional

from src.types import ExecutionResult, TransactionID


class HardwareExecutor:
    """Simulated hardware adapter with SpaceWire bandwidth throttling.

    Bandwidth is modelled by sleeping for ``len(payload) / (bandwidth_kbps * 1000 / 8)``
    seconds before acknowledging the command.
    """

    # Actions whose execution is modelled as asynchronous (long-running)
    ASYNC_ACTIONS = {"heat_to_40", "cool_down", "centrifuge", "pressure_test"}

    def __init__(
        self,
        bandwidth_kbps: int = 200,
        lab_id: str = "Fluid-Lab-Demo",
        action_handler: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self._bandwidth_kbps = bandwidth_kbps
        self._lab_id = lab_id
        # Pluggable handler so tests / demo can intercept actions
        self._action_handler = action_handler
        self._pending: Dict[str, Dict[str, Any]] = {}  # tx_id -> state

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    async def execute_instruction(
        self, payload: bytearray
    ) -> TransactionID:
        """Receive serialized instruction bytes and start execution.

        Always returns a TransactionID immediately.  Synchronous actions
        complete internally before this coroutine returns; async actions
        are scheduled as background tasks.
        """
        await self._simulate_transfer(payload)

        import json
        try:
            action_obj: Dict[str, Any] = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            action_obj = {"skill": "unknown", "params": {}}

        skill = action_obj.get("skill", "unknown")
        tx = TransactionID(
            tx_id=str(uuid.uuid4())[:8],
            subsystem=action_obj.get("subsystem", "unknown"),
            issued_at=time.time(),
        )
        self._pending[tx.tx_id] = {"status": "running", "action": action_obj}

        if skill in self.ASYNC_ACTIONS:
            asyncio.ensure_future(self._run_async(tx, action_obj))
        else:
            self._dispatch(skill, action_obj)
            self._pending[tx.tx_id]["status"] = "completed"

        return tx

    async def poll_transaction(self, tx: TransactionID) -> ExecutionResult:
        """Return current status of a previously issued transaction."""
        state = self._pending.get(tx.tx_id)
        if state is None:
            return ExecutionResult(status="not_found")
        return ExecutionResult(
            status=state["status"],
            total_steps=1,
            execution_log=[state["action"]],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _simulate_transfer(self, payload: bytearray) -> None:
        """Sleep to simulate SpaceWire bandwidth constraint."""
        bytes_per_second = (self._bandwidth_kbps * 1000) / 8
        delay = len(payload) / bytes_per_second
        if delay > 0:
            await asyncio.sleep(delay)

    async def _run_async(self, tx: TransactionID, action_obj: Dict[str, Any]) -> None:
        skill = action_obj.get("skill", "")
        # Simulate duration (e.g. heating takes 2 s in demo)
        await asyncio.sleep(action_obj.get("duration_s", 2.0))
        self._dispatch(skill, action_obj)
        self._pending[tx.tx_id]["status"] = "completed"

    def _dispatch(self, skill: str, action_obj: Dict[str, Any]) -> None:
        if self._action_handler is not None:
            self._action_handler(skill, action_obj)
