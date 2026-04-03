"""OutputController — Layer 3 serialization boundary.

This is the ONLY module permitted to serialize internal action objects
into bytearray payloads for the hardware layer.  No other module may
do JSON/binary encoding of actions.
"""
from __future__ import annotations

import json
import zlib
from typing import Any, Dict, List

from src.types import AgentDecision


class OutputController:
    """Converts internal AgentDecision objects into wire-format bytearrays."""

    def __init__(self, compress: bool = True):
        self._compress = compress

    def serialize(self, decision: AgentDecision) -> bytearray:
        """Serialize a single AgentDecision into a transmit-ready bytearray.

        Optionally applies zlib compression to respect SpaceWire bandwidth.
        chain-of-thought reasoning is stripped before serialization.
        """
        payload: Dict[str, Any] = {
            "skill": decision.skill,
            "action": decision.action,
            # reasoning is internal only — never sent over the wire
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self._compress:
            raw = zlib.compress(raw)
        return bytearray(raw)

    def deserialize(self, data: bytearray) -> Dict[str, Any]:
        """Inverse of serialize — used by HardwareExecutor."""
        raw = bytes(data)
        if self._compress:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                pass  # may already be uncompressed
        return json.loads(raw.decode("utf-8"))

    def generate_dag_json(self, dag_builder: Any) -> bytearray:
        """Serialize the execution DAG to a human-readable JSON bytearray.

        DAG output is intentionally kept uncompressed so downstream scheduling
        systems and operators can inspect the dependency graph directly.
        """
        raw = json.dumps(dag_builder.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        return bytearray(raw)

    def format_tree(self, plan_steps: List[Dict[str, Any]]) -> str:
        """Format the current plan tree for the Web Monitor SSE stream."""
        lines = ["=== AstroPlan Tree ==="]
        for i, step in enumerate(plan_steps, 1):
            status = step.get("status", "pending")
            skill = step.get("skill", "?")
            desc = step.get("description", "")
            marker = {"completed": "[v]", "running": "[>]", "failed": "[x]", "pending": "[ ]"}.get(
                status, "[ ]"
            )
            lines.append(f"  {marker} Step {i}: {skill}  {desc}")
        return "\n".join(lines)
