"""TelemetryBus — Layer 1 sensor ingestor.

Parsing incoming telemetry, handling out-of-order timestamps, and
raising DeviationEvents when safety thresholds are exceeded.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, Optional

from src.types import DeviationEvent


class TelemetryBus:
    """Accepts structured sensor updates and feeds Working Memory.

    For the MVP the stream is a JSON-encoded bytes blob rather than
    raw SpaceWire frames; the interface is kept identical so a future
    implementation can swap in a real decoder without touching callers.
    """

    def __init__(self, rules: Dict[str, Any], lab_id: str = ""):
        self._rules = rules          # threshold specs from InterlockEngine config
        self._lab_id = lab_id
        self._last_ts: int = 0      # tracks last accepted timestamp (ms)
        self._callbacks: list = []  # registered DeviationEvent listeners
        self._latest: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Stream API
    # ------------------------------------------------------------------

    def monitor_stream(self, stream: bytes, timestamp: int) -> Dict[str, Any]:
        """Parse one telemetry packet and return the decoded key-value dict.

        Packets with a timestamp older than the last accepted one are silently
        discarded to handle out-of-order space communication.
        """
        if timestamp < self._last_ts:
            # Stale / out-of-order packet — discard
            return {}

        self._last_ts = timestamp

        try:
            decoded: Dict[str, Any] = json.loads(stream.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

        decoded["_timestamp"] = timestamp
        self._latest.update(decoded)
        return decoded

    def check_threshold(
        self, telemetry: Dict[str, Any], rules: Optional[Dict[str, Any]] = None
    ) -> Optional[DeviationEvent]:
        """Compare sensor readings against safety limits.

        Returns the first DeviationEvent found, or None if all values are safe.
        """
        active_rules = rules if rules is not None else self._rules
        ts = int(time.time() * 1000)

        for key, spec in active_rules.items():
            value = telemetry.get(key)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            lo = spec.get("min")
            hi = spec.get("max")
            severity = spec.get("severity", "WARNING")

            breached = (lo is not None and value < lo) or (
                hi is not None and value > hi
            )
            if breached:
                evt = DeviationEvent(
                    sensor_key=key,
                    value=value,
                    threshold=hi if hi is not None else lo,
                    severity=severity,
                    timestamp=ts,
                )
                for cb in self._callbacks:
                    cb(evt)
                return evt

        return None

    def register_deviation_callback(self, cb: Callable[[DeviationEvent], None]) -> None:
        """Register a listener that is called whenever a threshold is exceeded."""
        self._callbacks.append(cb)

    def latest_snapshot(self) -> Dict[str, Any]:
        """Return a copy of the latest merged telemetry state."""
        return dict(self._latest)

    # ------------------------------------------------------------------
    # Mock stream helper (for testing / demo)
    # ------------------------------------------------------------------

    @staticmethod
    def make_packet(data: Dict[str, Any], timestamp: Optional[int] = None) -> tuple:
        """Create a (bytes, timestamp) pair for inject into monitor_stream."""
        ts = timestamp if timestamp is not None else int(time.time() * 1000)
        return json.dumps(data).encode("utf-8"), ts

    def apply_mock_update(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convenience helper for tests/demo: injects data directly into latest state."""
        self._latest.update(data)
        return dict(self._latest)
