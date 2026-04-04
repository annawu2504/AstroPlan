"""WahAdapter — thin wrapper around ReAcTree's WahUnityEnv.

Exposes a uniform interface (reset / step / get_skill_set / check_success)
that the AstroPlanEvaluator uses without knowing VirtualHome internals.

Guarded import: ``WahAdapter.available`` is False when VirtualHome / ReAcTree
is not installed, and instantiation raises ImportError.

ReAcTree reference: ReAcTree/src/wah/wah_env.py (WahUnityEnv)
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional import — ReAcTree WAH env
# ---------------------------------------------------------------------------

_REAC_SRC = str(pathlib.Path(__file__).parents[4] / "ReAcTree" / "src")

_WAH_AVAILABLE = False
_import_error: Optional[str] = None

try:
    if _REAC_SRC not in sys.path:
        sys.path.insert(0, _REAC_SRC)
    from wah.wah_env import WahUnityEnv            # type: ignore[import]
    from wah.wah_utils import check_goal_condition  # type: ignore[import]
    _WAH_AVAILABLE = True
except ImportError as _exc:
    _import_error = str(_exc)


# ---------------------------------------------------------------------------
# WahAdapter
# ---------------------------------------------------------------------------

class WahAdapter:
    """Wraps WahUnityEnv for use by AstroPlanEvaluator.

    Interface
    ---------
    reset(task_d)         → initial_obs_text: str
    step(nl_skill)        → {"text": str, "success": bool}
    get_skill_set()       → List[str]   (possible skill set for current state)
    check_success()       → (goal_sr: float, subgoal_sr: float)

    Parameters
    ----------
    cfg:
        Dict matching ReAcTree's environment config keys.
        Minimum required::

            {"environment": {
                "observation_types": ["partial"],
                "use_editor": False,
                "base_port": 8080,
                "port_id": 0,
                "executable_args": {},
                "recording_options": {"recording": False}
             }}

    Attributes
    ----------
    available : bool
        Class-level flag — False when VirtualHome / ReAcTree unavailable.
    """

    available: bool = _WAH_AVAILABLE

    def __init__(self, cfg: Dict[str, Any]) -> None:
        if not _WAH_AVAILABLE:
            raise ImportError(
                f"WahAdapter: WAH-NL / VirtualHome not available. "
                f"Original error: {_import_error}. "
                f"Install the VirtualHome submodule and virtualhome package."
            )
        from types import SimpleNamespace

        def _ns(d: dict) -> Any:
            ns = SimpleNamespace()
            for k, v in d.items():
                setattr(ns, k, _ns(v) if isinstance(v, dict) else v)
            return ns

        self._cfg_ns = _ns(cfg)
        self._env = WahUnityEnv(self._cfg_ns)
        self._task_d: Optional[Dict] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def reset(self, task_d: Dict[str, Any]) -> str:
        """Reset environment for a new task.  Returns initial observation text."""
        self._task_d = task_d
        self._env.reset(task_d)
        obs = self._env.get_init_obs()
        return obs.get("text", "")

    def step(self, nl_skill: str) -> Dict[str, Any]:
        """Execute one NL skill.  Returns {"text": ..., "success": bool}."""
        obs = self._env.step(nl_skill)
        return {
            "text": obs.get("text", ""),
            "success": obs.get("possible", True),
        }

    def get_skill_set(self) -> List[str]:
        """Return the possible skill set for the current environment state."""
        if hasattr(self._env, "get_possible_skill_set"):
            return self._env.get_possible_skill_set()
        return []

    def check_success(self) -> Tuple[float, float]:
        """Return (goal_success_rate, subgoal_success_rate) for the current task.

        Mirrors WahEvaluator.evaluate_task_completion() from ReAcTree.
        Returns (0.0, 0.0) if the task data or graph are unavailable.
        """
        if self._task_d is None:
            return 0.0, 0.0
        try:
            task_goal = self._task_d.get("task_goal", {})
            graph_d = self._env.get_graph()
            graph = graph_d[1]  # WahUnityEnv returns (success, graph)
            name_id_dict_sim2nl = getattr(self._env, "name_id_dict_sim2nl", {})
            name_id_dict_nl2sim = getattr(self._env, "name_id_dict_nl2sim", {})
            subgoal_sr = check_goal_condition(
                task_goal, graph, name_id_dict_sim2nl, name_id_dict_nl2sim
            )
            goal_sr = 1.0 if subgoal_sr == 1.0 else 0.0
            return float(goal_sr), float(subgoal_sr)
        except Exception:
            return 0.0, 0.0
