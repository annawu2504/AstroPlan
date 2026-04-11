"""ManualParser — LLM-based experiment manual → Milestone extractor.

Research content one (论文研究内容一) core: use the LLM to semantically
decouple an experiment manual into 4-tuple Milestone units, as opposed to
learning milestones purely from execution history.

Typical usage
-------------
    parser = ManualParser(llm_client, registry)
    milestones = parser.parse(open("config/labs/Fluid-Lab-Demo/manual.txt").read(),
                              lab_id="Fluid-Lab-Demo")
    milestone_engine.build_index(milestones)

When ``llm_client`` is None (mock mode) or the LLM returns unparseable output,
``parse()`` returns an empty list so the caller can fall back gracefully.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from src.types import (
    AtomicSkillRecord,
    Milestone,
    MilestoneStateDescription,
    PhysicalConstraints,
    TaskVector,
    TrajectoryFragment,
)


class ManualParser:
    """Extract Milestone objects from a plain-text experiment manual.

    Parameters
    ----------
    llm_client:
        Any object with a ``call(prompt: str) -> str`` method.
        Pass ``None`` to disable LLM parsing (returns empty list).
    registry:
        MCPRegistry used to validate that extracted skill names actually exist.
        Skills not present in the registry are silently filtered out.
    """

    def __init__(self, llm_client: Any, registry: Any) -> None:
        self._llm = llm_client
        self._registry = registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, manual_text: str, lab_id: str) -> List[Milestone]:
        """Send *manual_text* to the LLM and extract Milestone objects.

        Returns an empty list when the LLM is unavailable, produces
        unparseable output, or no valid milestones can be extracted.

        Parameters
        ----------
        manual_text:
            Raw experiment manual content (plain text, any language).
        lab_id:
            Used as the ``mission_id`` in generated TaskVector objects.
        """
        if self._llm is None:
            return []

        available = self._registry.skill_names()
        if not available:
            return []

        prompt = self._build_prompt(manual_text, available)
        try:
            raw = self._llm.call(prompt)
        except Exception as exc:
            print(f"[ManualParser] LLM call failed: {exc}")
            return []

        return self._parse_json(raw, lab_id, set(available))

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, manual_text: str, skills: List[str]) -> str:
        skills_str = "\n".join(f"  - {s}" for s in skills)

        example = json.dumps(
            [
                {
                    "goal": "Activate pump and heat sample to 40°C",
                    "pre_states": {"fluid_pump": "IDLE"},
                    "steps": ["activate_pump", "heat_to_40"],
                    "post_states": {"fluid_pump": "ACTIVE", "thermal": "HEATING"},
                    "safety_thresholds": {"temperature": 45.0},
                },
            ],
            ensure_ascii=False,
            indent=2,
        )

        return (
            "You are a space-lab procedure analyst. Extract milestones from the "
            "experiment manual below.\n\n"
            "Available skills (use ONLY these exact names in 'steps'):\n"
            f"{skills_str}\n\n"
            "Manual text:\n"
            "---\n"
            f"{manual_text}\n"
            "---\n\n"
            "Output ONLY a JSON array where each element is a milestone:\n"
            f"{example}\n\n"
            "Rules:\n"
            "  1. 'steps' must contain only skill names from the list above.\n"
            "  2. 'pre_states' / 'post_states' map subsystem names to FSM state strings.\n"
            "  3. 'safety_thresholds' maps telemetry key to numeric limit (omit if none).\n"
            "  4. Each milestone should represent one coherent experimental phase.\n"
            "  5. Output NO extra text — only the JSON array.\n"
        )

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    def _parse_json(
        self, raw: str, lab_id: str, skills_set: set
    ) -> List[Milestone]:
        text = raw.strip()

        # Strip markdown code fences if present
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if fence:
            text = fence.group(1).strip()

        # Find the outermost JSON array using bracket counting
        start = text.find("[")
        if start == -1:
            print(
                f"[ManualParser] No JSON array found in LLM response. "
                f"raw[:200]={raw[:200]!r}"
            )
            return []

        depth = 0
        end = start
        for i, ch in enumerate(text[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        try:
            records: List[Dict[str, Any]] = json.loads(text[start:end])
        except json.JSONDecodeError as exc:
            print(f"[ManualParser] JSON decode error: {exc}. "
                  f"Extracted: {text[start:end][:300]!r}")
            return []

        if not isinstance(records, list):
            print(f"[ManualParser] Expected list, got {type(records).__name__}.")
            return []

        milestones: List[Milestone] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            m = self._record_to_milestone(rec, lab_id, skills_set)
            if m is not None:
                milestones.append(m)

        print(
            f"[ManualParser] Extracted {len(milestones)} milestone(s) "
            f"from {len(records)} record(s) (lab={lab_id})"
        )
        return milestones

    # ------------------------------------------------------------------
    # Record → Milestone conversion
    # ------------------------------------------------------------------

    def _record_to_milestone(
        self, rec: Dict[str, Any], lab_id: str, skills_set: set
    ) -> Optional[Milestone]:
        goal = str(rec.get("goal", "")).strip()
        if not goal:
            return None

        # Filter steps to only known skills
        raw_steps = rec.get("steps", [])
        valid_steps = [s for s in raw_steps if isinstance(s, str) and s in skills_set]
        if not valid_steps:
            # A milestone with no valid steps is useless for retrieval
            print(
                f"[ManualParser] Skipping milestone '{goal[:50]}': "
                "no valid skill names after filtering."
            )
            return None

        pre_states: Dict[str, str] = {
            str(k): str(v)
            for k, v in rec.get("pre_states", {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        post_states: Dict[str, str] = {
            str(k): str(v)
            for k, v in rec.get("post_states", {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        safety: Dict[str, float] = {}
        for k, v in rec.get("safety_thresholds", {}).items():
            try:
                safety[str(k)] = float(v)
            except (TypeError, ValueError):
                pass

        # Generate a stable content-hash ID
        step_key = "|".join(valid_steps)
        mid = hashlib.sha256(f"{lab_id}::{goal}::{step_key}".encode()).hexdigest()[:12]

        # Build BM25 keywords from goal + skill names
        keywords = list(
            dict.fromkeys(
                re.findall(r"[\w\u4e00-\u9fff]+", goal.lower())
                + [tok for s in valid_steps for tok in re.findall(r"[a-z]+", s.lower())]
            )
        )

        return Milestone(
            milestone_id=mid,
            task_vector=TaskVector(
                mission_id=lab_id,
                goal_text=goal,
                keywords=keywords,
            ),
            state_description=MilestoneStateDescription(
                subsystem_states=pre_states,
                completed_skills=[],
                description=f"pre-state for: {goal}",
            ),
            trajectory=TrajectoryFragment(
                steps=[AtomicSkillRecord(skill_name=s) for s in valid_steps],
                control_flow="Sequence",
                success_rate=1.0,   # manual-derived: assume authoritative
                observation_count=1,
            ),
            constraints=PhysicalConstraints(
                required_preconditions=pre_states,
                postconditions=post_states,
                safety_thresholds=safety,
                interruptible=True,
            ),
        )
