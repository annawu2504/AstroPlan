"""SkillLibrary — automated pattern extraction from historical task executions.

Observes successful execute_standalone() runs and promotes frequently occurring
skill sequences into Milestone objects that populate MilestoneEngine.

Design principles
-----------------
- Skills themselves are NOT auto-generated (they have real hardware side effects
  and require FSM registration in MCPRegistry).  What is extracted are *usage
  patterns*: which skills co-occur, in what order, and in what FSM context.
- A pattern becomes a Milestone only after MIN_PROMOTE observations so that
  one-off runs do not pollute the retrieval index.
- Persistence uses plain JSON so the library can be inspected and edited.

Typical call sequence in AstroPlan.execute_standalone()
--------------------------------------------------------
    before run:  state_before = env._memory.snapshot()
    after run:   state_after  = env._memory.snapshot()
                 skill_library.observe(steps, goal_text, state_before, state_after,
                                       constraints, success=True)
                 new_milestones = skill_library.export_milestones()
                 if new_milestones:
                     milestone_engine.build_index(new_milestones)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from src.types import (
    AtomicSkillRecord,
    Milestone,
    MilestoneStateDescription,
    PhysicalConstraints,
    TaskVector,
    TrajectoryFragment,
)


# ---------------------------------------------------------------------------
# Internal record (not exported — lives only in this module)
# ---------------------------------------------------------------------------

@dataclass
class _PatternRecord:
    milestone_id: str
    goal_text: str
    steps: List[AtomicSkillRecord]
    state_before: MilestoneStateDescription
    state_after: MilestoneStateDescription
    constraints: PhysicalConstraints
    observation_count: int = 1
    success_count: int = 1


# ---------------------------------------------------------------------------
# SkillLibrary
# ---------------------------------------------------------------------------

class SkillLibrary:
    """Extract and accumulate skill usage patterns from execution history.

    Parameters
    ----------
    lab_id:
        Used as mission_id in generated TaskVector objects.
    min_promote:
        Minimum number of observations before a pattern is included in
        export_milestones().  Default 2 prevents one-off runs from polluting
        the retrieval index.
    """

    MIN_PROMOTE_DEFAULT = 2

    def __init__(self, lab_id: str, min_promote: int = MIN_PROMOTE_DEFAULT) -> None:
        self._lab_id = lab_id
        self._min_promote = min_promote
        self._patterns: Dict[str, _PatternRecord] = {}

    # ------------------------------------------------------------------
    # Observation API — called after each completed mission
    # ------------------------------------------------------------------

    def observe(
        self,
        steps: List[AtomicSkillRecord],
        goal_text: str,
        state_before: MilestoneStateDescription,
        state_after: MilestoneStateDescription,
        constraints: PhysicalConstraints,
        *,
        success: bool = True,
    ) -> None:
        """Record one execution as an observation.

        Only successful executions are indexed; failed runs are silently
        discarded so the library does not learn from broken trajectories.

        Parameters
        ----------
        steps:
            Ordered list of AtomicSkillRecord objects executed during the run.
        goal_text:
            The natural-language mission goal (used as retrieval query target).
        state_before:
            World state snapshot before execution started.
        state_after:
            World state snapshot after execution completed.
        constraints:
            Physical FSM/safety constraints observed during the run.
        success:
            True if the mission completed without failure.
        """
        if not success or not steps:
            return

        mid = self._compute_id(goal_text, steps)
        if mid in self._patterns:
            rec = self._patterns[mid]
            rec.observation_count += 1
            rec.success_count += 1
            # Merge FSM postconditions if we see new state transitions
            rec.state_after.subsystem_states.update(state_after.subsystem_states)
        else:
            self._patterns[mid] = _PatternRecord(
                milestone_id=mid,
                goal_text=goal_text,
                steps=list(steps),
                state_before=state_before,
                state_after=state_after,
                constraints=constraints,
            )

    # ------------------------------------------------------------------
    # Export API
    # ------------------------------------------------------------------

    def export_milestones(self) -> List[Milestone]:
        """Return all patterns that have reached the promotion threshold.

        Converts each _PatternRecord into a fully typed Milestone object.
        Called by AstroPlan after each execute_standalone() to refresh the
        MilestoneEngine index.
        """
        milestones: List[Milestone] = []
        for rec in self._patterns.values():
            if rec.observation_count < self._min_promote:
                continue

            keywords = self._extract_keywords(rec.goal_text, rec.steps)
            success_rate = rec.success_count / rec.observation_count

            milestones.append(Milestone(
                milestone_id=rec.milestone_id,
                task_vector=TaskVector(
                    mission_id=self._lab_id,
                    goal_text=rec.goal_text,
                    keywords=keywords,
                ),
                state_description=rec.state_before,
                trajectory=TrajectoryFragment(
                    steps=list(rec.steps),
                    control_flow="Sequence",
                    success_rate=success_rate,
                    observation_count=rec.observation_count,
                ),
                constraints=rec.constraints,
            ))
        return milestones

    def pattern_count(self) -> int:
        """Total number of observed patterns (including unpromoted ones)."""
        return len(self._patterns)

    def promoted_count(self) -> int:
        """Number of patterns that have reached the promotion threshold."""
        return sum(
            1 for rec in self._patterns.values()
            if rec.observation_count >= self._min_promote
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Persist the pattern store to a JSON file."""
        data: Dict[str, Any] = {}
        for mid, rec in self._patterns.items():
            data[mid] = {
                "goal_text": rec.goal_text,
                "steps": [
                    {
                        "skill_name": s.skill_name,
                        "params": s.params,
                        "subsystem": s.subsystem,
                        "duration_ms": s.duration_ms,
                    }
                    for s in rec.steps
                ],
                "state_before": {
                    "subsystem_states": rec.state_before.subsystem_states,
                    "completed_skills": rec.state_before.completed_skills,
                    "description": rec.state_before.description,
                },
                "state_after": {
                    "subsystem_states": rec.state_after.subsystem_states,
                    "completed_skills": rec.state_after.completed_skills,
                    "description": rec.state_after.description,
                },
                "constraints": {
                    "required_preconditions": rec.constraints.required_preconditions,
                    "postconditions": rec.constraints.postconditions,
                    "safety_thresholds": rec.constraints.safety_thresholds,
                    "interruptible": rec.constraints.interruptible,
                },
                "observation_count": rec.observation_count,
                "success_count": rec.success_count,
            }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def load(self, path: str) -> None:
        """Load a previously persisted pattern store, merging with any existing data."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data: Dict[str, Any] = json.load(fh)
        except FileNotFoundError:
            return  # no-op if file does not yet exist

        for mid, raw in data.items():
            steps = [
                AtomicSkillRecord(
                    skill_name=s["skill_name"],
                    params=s.get("params", {}),
                    subsystem=s.get("subsystem", ""),
                    duration_ms=s.get("duration_ms", 0),
                )
                for s in raw.get("steps", [])
            ]
            sb = raw.get("state_before", {})
            sa = raw.get("state_after", {})
            con = raw.get("constraints", {})
            if mid in self._patterns:
                # Merge counts when reloading over an already-running library
                self._patterns[mid].observation_count = max(
                    self._patterns[mid].observation_count,
                    raw.get("observation_count", 1),
                )
                self._patterns[mid].success_count = max(
                    self._patterns[mid].success_count,
                    raw.get("success_count", 1),
                )
            else:
                self._patterns[mid] = _PatternRecord(
                    milestone_id=mid,
                    goal_text=raw.get("goal_text", ""),
                    steps=steps,
                    state_before=MilestoneStateDescription(
                        subsystem_states=sb.get("subsystem_states", {}),
                        completed_skills=sb.get("completed_skills", []),
                        description=sb.get("description", ""),
                    ),
                    state_after=MilestoneStateDescription(
                        subsystem_states=sa.get("subsystem_states", {}),
                        completed_skills=sa.get("completed_skills", []),
                        description=sa.get("description", ""),
                    ),
                    constraints=PhysicalConstraints(
                        required_preconditions=con.get("required_preconditions", {}),
                        postconditions=con.get("postconditions", {}),
                        safety_thresholds=con.get("safety_thresholds", {}),
                        interruptible=con.get("interruptible", True),
                    ),
                    observation_count=raw.get("observation_count", 1),
                    success_count=raw.get("success_count", 1),
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_id(goal_text: str, steps: List[AtomicSkillRecord]) -> str:
        """Stable content-hash ID for a (goal, skill_sequence) pair."""
        step_key = "|".join(s.skill_name for s in steps)
        raw = f"{goal_text}::{step_key}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    @staticmethod
    def _extract_keywords(
        goal_text: str, steps: List[AtomicSkillRecord]
    ) -> List[str]:
        """BM25 keyword set: goal words + skill names + param keys."""
        import re
        words = re.findall(r"[\w\u4e00-\u9fff]+", goal_text.lower())
        skill_tokens = []
        for step in steps:
            skill_tokens += re.findall(r"[a-z]+", step.skill_name.lower())
            skill_tokens += list(step.params.keys())
        return list(dict.fromkeys(words + skill_tokens))  # deduplicated, order-preserving
