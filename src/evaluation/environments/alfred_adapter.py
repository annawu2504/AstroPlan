"""AlfredAdapter — thin wrapper around ReAcTree's ThorConnector.

Exposes a uniform interface (reset / step / get_skill_set / check_success)
that the AstroPlanEvaluator uses without knowing ai2thor internals.

The adapter is guarded: if ReAcTree or ai2thor is not installed, the class
still exists but ``AlfredAdapter.available`` is False and instantiation
raises ImportError.  The evaluator checks this flag before running.

ReAcTree reference: ReAcTree/src/alfred/alfred_env.py (ThorConnector)
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Optional import — ReAcTree ALFRED env
# ---------------------------------------------------------------------------

_REAC_SRC = str(pathlib.Path(__file__).parents[4] / "ReAcTree" / "src")

_ALFRED_AVAILABLE = False
_import_error: Optional[str] = None

try:
    if _REAC_SRC not in sys.path:
        sys.path.insert(0, _REAC_SRC)
    from alfred.alfred_env import ThorConnector          # type: ignore[import]
    from alfred.alfred_llm_agent import AlfredLlmAgent  # type: ignore[import]
    _ALFRED_AVAILABLE = True
except ImportError as _exc:
    _import_error = str(_exc)


# ---------------------------------------------------------------------------
# AlfredAdapter
# ---------------------------------------------------------------------------

class AlfredAdapter:
    """Wraps ThorConnector for use by AstroPlanEvaluator.

    Interface
    ---------
    reset(task_d)       → initial_obs_text: str
    step(nl_skill)      → {"text": str, "success": bool}
    get_skill_set(obs)  → List[str]   (NL skill names for the current state)
    check_success()     → bool        (True if task goal satisfied)

    Parameters
    ----------
    cfg:
        Dict matching ReAcTree's llm_agent/alfred config keys.
        Minimum required::

            {"alfred": {"splits": "alfred/data/splits/oct21.json"},
             "environment": {},
             "llm_agent": {"working_memory": False}}

    Attributes
    ----------
    available : bool
        Class-level flag — False when ai2thor / ReAcTree unavailable.
    """

    available: bool = _ALFRED_AVAILABLE

    def __init__(self, cfg: Dict[str, Any]) -> None:
        if not _ALFRED_AVAILABLE:
            raise ImportError(
                f"AlfredAdapter: ALFRED / ai2thor not available. "
                f"Original error: {_import_error}. "
                f"Install: pip install ai2thor==2.1.0"
            )
        from types import SimpleNamespace

        def _ns(d: dict) -> Any:
            """Recursively convert nested dicts to SimpleNamespace."""
            ns = SimpleNamespace()
            for k, v in d.items():
                setattr(ns, k, _ns(v) if isinstance(v, dict) else v)
            return ns

        self._cfg_ns = _ns(cfg)
        self._env = ThorConnector(self._cfg_ns)
        self._llm_agent: Optional[Any] = None
        self._task_d: Optional[Dict] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def reset(self, task_d: Dict[str, Any]) -> str:
        """Reset the environment for a new task.  Returns initial observation."""
        self._task_d = task_d
        obs = self._env.init_reset(task_d)
        return obs.get("text", obs.get("message", ""))

    def step(self, nl_skill: str) -> Dict[str, Any]:
        """Execute one NL skill.  Returns {"text": ..., "success": bool}."""
        obs = self._env.llm_skill_interact(nl_skill)
        return {
            "text": obs.get("message", ""),
            "success": obs.get("success", False),
        }

    def get_skill_set(self, obs: Dict[str, Any]) -> List[str]:
        """Return the skill set valid for the current environment state.

        Delegates to AlfredLlmAgent.update_skill_set() if an agent is
        attached; otherwise returns an empty list (caller must manage skills).
        """
        if self._llm_agent is not None and hasattr(self._llm_agent, "update_skill_set"):
            return self._llm_agent.update_skill_set(obs)
        return []

    def check_success(self) -> bool:
        """Return True if the current task goal is satisfied.

        Delegates to ThorConnector's task success evaluation if available.
        Falls back to False (conservative) when the check is unavailable.
        """
        if hasattr(self._env, "task_success"):
            return bool(self._env.task_success())
        # ai2thor reports success via the event object after the last action
        if hasattr(self._env, "last_event") and self._env.last_event is not None:
            return bool(getattr(self._env.last_event.metadata, "lastActionSuccess", False))
        return False

    def attach_llm_agent(self, agent: Any) -> None:
        """Optionally attach an AlfredLlmAgent for skill-set generation."""
        self._llm_agent = agent
