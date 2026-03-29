"""TaskDataset — Layer 1 task ingestor.

Parses raw scientific experiment requirements into a natural-language
global goal string that is injected into the agent tree root.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TaskDataset:
    """Holds one parsed experiment task."""
    nl_global_goal: str
    raw_requirements: str

    @classmethod
    def parse_requirements(cls, raw_requirements: str) -> "TaskDataset":
        """Precisely parse long-horizon experiment requirements.

        In the MVP the raw text is used directly as the global goal;
        a real implementation would segment multi-phase procedures.
        """
        # Strip blank lines and leading/trailing whitespace
        lines = [ln.strip() for ln in raw_requirements.splitlines() if ln.strip()]
        nl_global_goal = " ".join(lines)
        return cls(nl_global_goal=nl_global_goal, raw_requirements=raw_requirements)
