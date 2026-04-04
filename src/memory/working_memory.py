"""WorkingMemory — Layer 3 global working memory.

Fuses multi-source telemetry and action history into a single
SharedContext object that all agent nodes read from.
"""
from __future__ import annotations

import copy
import time
from typing import Any, Dict, List

from src.types import SharedContext


class WorkingMemory:
    """Real-time fusion of sensor data and action history.

    This is the single authoritative source of SharedContext for the
    entire agent tree.  Deep nodes must not maintain their own state
    copies to avoid information isolation and LLM hallucination.
    """

    def __init__(self, lab_id: str):
        self._lab_id = lab_id
        self._telemetry: Dict[str, Any] = {}
        self._subsystem_states: Dict[str, str] = {}
        self._action_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def update_telemetry(self, sensor_updates: Dict[str, Any]) -> None:
        """Merge validated sensor readings into the telemetry snapshot."""
        self._telemetry.update(sensor_updates)

    def update_subsystem_state(self, subsystem: str, state: str) -> None:
        """Record a subsystem FSM state change."""
        old = self._subsystem_states.get(subsystem)
        self._subsystem_states[subsystem] = state
        if old != state:
            print(
                f"[{self._lab_id}] 遥测更新: {subsystem} {old} \u2192 {state}"
            )

    def log_action(self, action: Dict[str, Any]) -> None:
        """Append a completed action to the history log."""
        self._action_log.append({**action, "_ts": int(time.time() * 1000)})

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def snapshot(self) -> SharedContext:
        """Return the current strongly-typed SharedContext."""
        return SharedContext(
            lab_id=self._lab_id,
            telemetry=dict(self._telemetry),
            subsystem_states=dict(self._subsystem_states),
            action_log=[copy.deepcopy(entry) for entry in self._action_log],
            timestamp=int(time.time() * 1000),
        )
