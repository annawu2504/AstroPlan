"""MilestoneEngine — Layer 3 expert trajectory retrieval.

Builds an offline BM25 index over Milestone objects and retrieves the most
relevant milestones online to guide AgentNode planning.

Each Milestone is now a full 4-tuple:
    task_vector          — BM25/dense retrieval key
    state_description    — world-state precondition
    trajectory           — ordered AtomicSkillRecord steps
    constraints          — FSM pre/postconditions and safety thresholds

The retrieval pipeline:
    1. build_index(milestones)           — offline; tokenises task_vector.keywords
    2. filter_applicable(current_states) — drop milestones whose preconditions mismatch
    3. retrieve(query_state, goal)       — BM25 rank; return top-k Milestone objects

BM25 is the default because it requires no embedding API key.  Dense retrieval
can be layered on later by populating task_vector.embedding and overriding
_score().
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from src.types import Milestone, MilestoneStateDescription


class MilestoneEngine:
    """Offline index builder + online retrieval for 4-tuple Milestones."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._milestones: List[Milestone] = []
        self._tokens_per_doc: List[List[str]] = []   # parallel to _milestones
        self._idf: Dict[str, float] = {}
        self._avg_dl: float = 0.0

    # ------------------------------------------------------------------
    # Offline phase
    # ------------------------------------------------------------------

    def build_index(self, milestones: List[Milestone]) -> None:
        """Parse Milestone objects and build BM25 index.

        Indexes on task_vector.keywords + goal_text tokens.
        Previous index is replaced entirely.
        """
        self._milestones = list(milestones)
        self._tokens_per_doc = []
        df: Dict[str, int] = defaultdict(int)

        for m in self._milestones:
            tokens = self._tokenize_milestone(m)
            self._tokens_per_doc.append(tokens)
            for t in set(tokens):
                df[t] += 1

        n = len(self._milestones)
        self._avg_dl = (
            sum(len(tok) for tok in self._tokens_per_doc) / n if n > 0 else 1.0
        )
        self._idf = {
            t: math.log((n - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
            for t in df
        }

    # ------------------------------------------------------------------
    # Filtering phase — drop structurally incompatible milestones
    # ------------------------------------------------------------------

    def filter_applicable(
        self,
        current_subsystem_states: Dict[str, str],
    ) -> List[Milestone]:
        """Return milestones whose preconditions are satisfied by current FSM states.

        A milestone is applicable if every key in
        constraints.required_preconditions maps to the correct current state.
        Milestones with no preconditions are always applicable.
        """
        applicable: List[Milestone] = []
        for m in self._milestones:
            pre = m.constraints.required_preconditions
            if not pre:
                applicable.append(m)
                continue
            if all(
                current_subsystem_states.get(sub) == required
                for sub, required in pre.items()
            ):
                applicable.append(m)
        return applicable

    # ------------------------------------------------------------------
    # Online retrieval phase
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query_state: Dict[str, Any],
        goal: str,
        top_k: int = 3,
        current_subsystem_states: Optional[Dict[str, str]] = None,
    ) -> List[Milestone]:
        """Return up to top_k milestones ranked by BM25 + trajectory length penalty.

        Parameters
        ----------
        query_state:
            Current SharedContext.telemetry dict (unused in BM25 path; reserved
            for future dense retrieval that conditions on sensor readings).
        goal:
            The natural-language sub-goal used as the BM25 query.
        top_k:
            Maximum number of results to return.
        current_subsystem_states:
            When provided, milestones that fail filter_applicable() are excluded.
        """
        if not self._milestones:
            return []

        candidates = (
            self.filter_applicable(current_subsystem_states)
            if current_subsystem_states is not None
            else self._milestones
        )
        if not candidates:
            return []

        # Build a mapping from milestone object id to its token list
        mid_to_tokens = {
            id(m): toks
            for m, toks in zip(self._milestones, self._tokens_per_doc)
        }

        q_tokens = self._tokenize(goal)
        scored: List[tuple] = []  # (score, milestone)

        for m in candidates:
            tokens = mid_to_tokens.get(id(m), [])
            dl = len(tokens)
            tf_map: Dict[str, int] = defaultdict(int)
            for t in tokens:
                tf_map[t] += 1

            bm25 = 0.0
            for t in q_tokens:
                if t not in self._idf:
                    continue
                tf = tf_map[t]
                idf = self._idf[t]
                tf_norm = tf * (self._k1 + 1) / (
                    tf + self._k1 * (1 - self._b + self._b * dl / self._avg_dl)
                )
                bm25 += idf * tf_norm

            # Shorter trajectories preferred (less commitment, more flexible)
            traj_len = len(m.trajectory.steps)
            length_penalty = 1.0 / (1.0 + 0.05 * traj_len)
            # Higher success_rate boosts score
            quality_boost = 0.5 + 0.5 * m.trajectory.success_rate
            scored.append((bm25 * length_penalty * quality_boost, m))

        scored.sort(key=lambda x: x[0], reverse=True)

        result: List[Milestone] = []
        for sc, m in scored[:top_k]:
            if sc <= 0:
                break
            # Attach retrieval score to a copy so the stored object is unchanged
            import dataclasses
            result.append(dataclasses.replace(m, score=sc))
        return result

    # ------------------------------------------------------------------
    # Tokenisation helpers
    # ------------------------------------------------------------------

    def _tokenize_milestone(self, m: Milestone) -> List[str]:
        """Extract all BM25-indexable tokens from one Milestone."""
        tokens = self._tokenize(m.task_vector.goal_text)
        tokens += list(m.task_vector.keywords)
        # Also index skill names from the trajectory as content signals
        for step in m.trajectory.steps:
            tokens += self._tokenize(step.skill_name)
        return tokens

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
