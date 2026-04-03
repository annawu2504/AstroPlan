"""GroundCommandReceiver — Layer 5 ground command ingestor.

Parses priority-tagged ground commands and emits EventTriggerSignals
that drive the replanning machinery.
"""
from __future__ import annotations

from typing import Any, Dict

from src.types import EventTriggerSignal


class GroundCommandReceiver:
    """Parses incoming ground commands and decides whether to trigger replanning."""

    def receive(
        self,
        command: Dict[str, Any],
        preemptive: bool = False,
    ) -> EventTriggerSignal:
        """Parse a ground command dict and return an EventTriggerSignal.

        Parameters
        ----------
        command:
            Dict with at minimum ``{'type': str, 'priority': int, ...}``.
        preemptive:
            Whether this command should preempt the current execution.
        """
        cmd_type = command.get("type", "UNKNOWN")
        priority = int(command.get("priority", 1))
        scope = command.get("scope", "global")
        payload = command.get("payload", {})

        # Determine trigger type from command type
        if cmd_type in ("ABORT", "EMERGENCY_STOP"):
            preemptive = True
            priority = max(priority, 10)  # safety commands always high priority

        signal = EventTriggerSignal(
            source=cmd_type,
            priority=priority,
            payload=payload,
            preemptive=preemptive,
        )

        print(
            f"[GroundCmd] Received '{cmd_type}' | priority={priority} "
            f"| preemptive={preemptive} | scope={scope}"
        )
        return signal
