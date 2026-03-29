"""MilestoneEngine — Layer 3 expert trajectory retrieval.

Builds an offline index of expert demonstration trajectories and
retrieve relevant milestones online to guide AgentNode planning.

MVP uses BM25 keyword matching instead of dense vectors so no
embedding API key is required at demo time.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional

from src.types import Milestone


class MilestoneEngine:
    """Offline index builder + online retrieval for expert trajectories."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """BM25 hyper-parameters."""
        self._k1 = k1
        self._b = b
        self._corpus: List[Dict[str, Any]] = []  # [{goal, trajectory, tokens}]
        self._idf: Dict[str, float] = {}
        self._avg_dl: float = 0.0

    # ------------------------------------------------------------------
    # Offline phase
    # ------------------------------------------------------------------

    def build_index(self, expert_demos: List[Dict[str, Any]]) -> None:
        """Parse expert demonstrations and build BM25 index.

        Each demo should have 'goal' (str) and 'trajectory' (list of dicts).
        """
        self._corpus = []
        df: Dict[str, int] = defaultdict(int)

        for demo in expert_demos:
            tokens = self._tokenize(demo.get("goal", ""))
            for t in set(tokens):
                df[t] += 1
            self._corpus.append(
                {"goal": demo.get("goal", ""), "trajectory": demo.get("trajectory", []), "tokens": tokens}
            )

        n = len(self._corpus)
        self._avg_dl = (
            sum(len(d["tokens"]) for d in self._corpus) / n if n > 0 else 1.0
        )
        self._idf = {
            t: math.log((n - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
            for t in df
        }

    def _tokenize(self, text: str) -> List[str]:
        import re
        return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())

    # ------------------------------------------------------------------
    # Online phase
    # ------------------------------------------------------------------

    def retrieve(
        self, query_state: Dict[str, Any], goal: str, top_k: int = 3
    ) -> List[Milestone]:
        """Return up to *top_k* milestones ranked by BM25 + length penalty."""
        if not self._corpus:
            return []

        q_tokens = self._tokenize(goal)
        scores: List[float] = []

        for doc in self._corpus:
            dl = len(doc["tokens"])
            score = 0.0
            tf_map: Dict[str, int] = defaultdict(int)
            for t in doc["tokens"]:
                tf_map[t] += 1
            for t in q_tokens:
                if t not in self._idf:
                    continue
                tf = tf_map[t]
                idf = self._idf[t]
                tf_norm = tf * (self._k1 + 1) / (
                    tf + self._k1 * (1 - self._b + self._b * dl / self._avg_dl)
                )
                score += idf * tf_norm
            # Length penalty: shorter expert trajectories preferred
            traj_len = len(doc["trajectory"])
            length_penalty = 1.0 / (1.0 + 0.05 * traj_len)
            scores.append(score * length_penalty)

        ranked = sorted(
            zip(scores, self._corpus), key=lambda x: x[0], reverse=True
        )
        return [
            Milestone(goal=doc["goal"], trajectory=doc["trajectory"], score=sc)
            for sc, doc in ranked[:top_k]
            if sc > 0
        ]
