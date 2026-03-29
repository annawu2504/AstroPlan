"""HITLSuspensionOperator — Layer 5 human-in-the-loop suspension gate.

Suspends execution before irreversible operations, waits for human
feedback, and resumes with updated constraints.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.types import InterventionSignal, ResumeSignal


class HITLSuspensionOperator:
    """Blocks the execution pipeline at critical irreversible nodes.

    Usage::

        operator = HITLSuspensionOperator(timeout_s=300)
        resume = await operator.suspend(
            critical_state="pre_centrifuge",
            intervention=InterventionSignal(...),
        )
        if resume.approved:
            proceed_with_centrifuge()
    """

    def __init__(self, timeout_s: int = 300):
        self._timeout_s = timeout_s
        self._pending: Optional[asyncio.Future] = None

    async def suspend(
        self,
        critical_state: str,
        intervention: InterventionSignal,
    ) -> ResumeSignal:
        """Suspend execution and wait for a human resume signal.

        If *timeout_s* elapses with no response the system stays suspended
        (returns a ResumeSignal with ``approved=False``).
        """
        print(
            f"[HITL] \u26a0\ufe0f  SUSPENDED at critical state: '{critical_state}'\n"
            f"       Reason: {intervention.reason}\n"
            f"       Awaiting human approval (timeout={self._timeout_s}s)..."
        )

        loop = asyncio.get_event_loop()
        self._pending = loop.create_future()

        try:
            result: ResumeSignal = await asyncio.wait_for(
                asyncio.shield(self._pending), timeout=self._timeout_s
            )
            return result
        except asyncio.TimeoutError:
            print(f"[HITL] Timeout after {self._timeout_s}s — staying suspended.")
            return ResumeSignal(approved=False, updated_constraints=None)
        finally:
            self._pending = None

    def resume(self, approved: bool, updated_constraints: Optional[Dict[str, Any]] = None) -> None:
        """Called externally (e.g. from the Web Monitor) to unblock suspension."""
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(
                ResumeSignal(approved=approved, updated_constraints=updated_constraints)
            )
