"""LatencyObserver — Layer 4 latency estimation and preemption decision.

Monitors the round-trip command latency and decides whether the current
agent in control should yield to a higher-priority ground command.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional, Tuple

from src.types import EventTriggerSignal


class LatencyObserver:
    """Sliding-window latency estimator with preemption gating."""

    def __init__(self, threshold_ms: int = 5000, window: int = 10):
        """Parameters
        ----------
        threshold_ms:
            If estimated RTT exceeds this value, flag for preemption.
        window:
            Number of recent samples used for the rolling average.
        """
        self._threshold_ms = threshold_ms
        self._samples: Deque[float] = deque(maxlen=window)

    # ------------------------------------------------------------------
    # Measurement API
    # ------------------------------------------------------------------

    def record_rtt(self, rtt_ms: float) -> None:
        """Record one round-trip time measurement in milliseconds."""
        self._samples.append(rtt_ms)

    def estimated_rtt(self) -> Optional[float]:
        """Return the rolling average RTT, or None if no samples yet."""
        if not self._samples:
            return None
        return sum(self._samples) / len(self._samples)

    # ------------------------------------------------------------------
    # Preemption decision
    # ------------------------------------------------------------------

    def should_preempt(
        self,
        incoming: EventTriggerSignal,
        current_priority: int,
    ) -> Tuple[bool, str]:
        """Decide whether *incoming* trigger should preempt current execution.

        Returns ``(preempt: bool, reason: str)``.
        """
        rtt = self.estimated_rtt()

        # High-latency environment: favour ground commands more aggressively
        latency_factor = 1.0
        if rtt is not None and rtt > self._threshold_ms:
            latency_factor = 1.5

        effective_priority = incoming.priority * latency_factor

        if incoming.preemptive and effective_priority > current_priority:
            return True, (
                f"Preemptive trigger priority {incoming.priority} "
                f"(effective {effective_priority:.1f}) > current {current_priority}; "
                f"RTT={rtt:.0f}ms" if rtt else "RTT unknown"
            )

        return False, "No preemption required"
