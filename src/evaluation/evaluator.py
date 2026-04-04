"""AstroPlanEvaluator — end-to-end evaluation runner for ALFRED and WAH-NL.

Drives the AstroPlan agent tree against real environment simulators and
computes metrics comparable to ReAcTree's evaluation pipeline.

Architecture overview
---------------------
                     eval_config.yaml
                           │
                    EvalConfig (dataclass)
                           │
                  AstroPlanEvaluator
                     │         │
               env adapter   LLMBackend
              (alfred/wah)  (ollama/hf/anthropic/mock)
                     │         │
             EnvMCPBridge  AgentNode
                     │         │
             LaboratoryEnvironment.run()
                           │
                     EvalResult (per task)
                           │
                  aggregate_metrics()

Key design decisions vs ReAcTree
---------------------------------
- ReAcTree couples env and LLM agent into a single AgentNode subclass
  (WahAgentNode / AlfredAgentNode).  AstroPlanEvaluator keeps them
  separate: the env is wired through EnvMCPBridge into MCPRegistry, and
  the LLM backend is injected into AgentNode.  This preserves AstroPlan's
  layered, stateless node design.

- ReAcTree's constrained generation (guidance.select) is not replicated;
  AstroPlan uses JSON parsing + graceful degradation.

- Metrics match ReAcTree for fair comparison:
    ALFRED:  success_rate
    WAH-NL:  goal_success_rate, subgoal_success_rate
    Both:    avg_replan_count, avg_tree_max_depth, lineage_stability

Usage::

    from src.evaluation.evaluator import load_eval_config, AstroPlanEvaluator

    cfg = load_eval_config("config/eval_config.yaml")
    evaluator = AstroPlanEvaluator(cfg)
    metrics = evaluator.evaluate()
    print(metrics)
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# EvalConfig
# ---------------------------------------------------------------------------

@dataclass
class EvalConfig:
    """Typed evaluation configuration.

    Matches the structure of config/eval_config.yaml.
    """
    dataset_type: str = "wah"          # "alfred" | "wah"
    testset_path: str = ""             # path to JSON test set file
    task_planner: str = "astroplan"    # "astroplan" (only option for now)
    max_steps: int = 50
    max_decisions: int = 100
    max_depth: int = 10
    eval_portion_pct: int = 100        # 1-100: percentage of test set to run
    random_seed: int = 0
    output_dir: str = "outputs/eval"
    backend: Dict[str, Any] = field(default_factory=lambda: {"backend": "mock"})


def load_eval_config(path: str = "config/eval_config.yaml") -> EvalConfig:
    """Load EvalConfig from a YAML file.

    Falls back to defaults if the file does not exist.
    Resolves ${ENV_VAR} placeholders in string values.
    """
    import re
    _env_re = re.compile(r"\$\{(\w+)\}")

    def _resolve(v: Any) -> Any:
        if isinstance(v, str):
            return _env_re.sub(lambda m: os.environ.get(m.group(1), m.group(0)), v)
        if isinstance(v, dict):
            return {k: _resolve(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_resolve(i) for i in v]
        return v

    if not pathlib.Path(path).exists():
        print(f"[EvalConfig] {path} not found — using defaults.")
        return EvalConfig()

    if yaml is None:
        raise ImportError("load_eval_config requires PyYAML: pip install pyyaml")

    with open(path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    raw = _resolve(raw)
    return EvalConfig(
        dataset_type=raw.get("dataset_type", "wah"),
        testset_path=raw.get("testset_path", ""),
        task_planner=raw.get("task_planner", "astroplan"),
        max_steps=raw.get("max_steps", 50),
        max_decisions=raw.get("max_decisions", 100),
        max_depth=raw.get("max_depth", 10),
        eval_portion_pct=raw.get("eval_portion_pct", 100),
        random_seed=raw.get("random_seed", 0),
        output_dir=raw.get("output_dir", "outputs/eval"),
        backend=raw.get("backend", {"backend": "mock"}),
    )


# ---------------------------------------------------------------------------
# EnvMCPBridge
# ---------------------------------------------------------------------------

class EnvMCPBridge:
    """Duck-typed MCPRegistry replacement that routes all skill calls to an env adapter.

    AgentNode._execute_action() calls env._mcp.has_skill(skill) and
    env._mcp.call(skill, params).  This bridge always returns True for
    has_skill and forwards the NL skill string to the env adapter's step().

    This allows any NL skill the LLM generates to be executed without
    pre-registering the ALFRED / WAH skill vocabulary in advance.

    Parameters
    ----------
    adapter:
        AlfredAdapter or WahAdapter instance (or any object with step(str)).
    """

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self._call_log: List[Dict[str, Any]] = []

    def has_skill(self, skill: str) -> bool:
        return True  # all NL skills pass through to the env adapter

    def call(self, skill: str, params: Dict[str, Any]) -> Dict[str, Any]:
        result = self._adapter.step(skill)
        self._call_log.append({"skill": skill, "params": params, "result": result})
        return result

    def skill_names(self) -> List[str]:
        return []  # dynamic — populated by env at each step


# ---------------------------------------------------------------------------
# PassthroughInterlock
# ---------------------------------------------------------------------------

class PassthroughInterlock:
    """Dummy InterlockEngine that allows all actions unconditionally.

    ALFRED / WAH have their own action validation inside the simulator.
    Using a passthrough here avoids coupling FSM rules (designed for
    the Fluid-Lab-Demo) to external benchmark environments.
    """

    def validate_action(self, skill: str) -> None:
        pass  # always allow

    def current_states(self) -> Dict[str, str]:
        return {}


# ---------------------------------------------------------------------------
# EvalResult (per-task)
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    task_id: Any
    nl_inst: str
    success: bool
    goal_success_rate: float        # WAH-NL only; 1.0 / 0.0
    subgoal_success_rate: float     # WAH-NL only; fraction of subgoals done
    replan_count: int
    tree_max_depth: int
    lineage_ids_seen: List[str] = field(default_factory=list)
    wall_time_s: float = 0.0
    terminate_reason: str = ""


# ---------------------------------------------------------------------------
# AstroPlanEvaluator
# ---------------------------------------------------------------------------

class AstroPlanEvaluator:
    """End-to-end evaluator for ALFRED and WAH-NL benchmarks.

    Parameters
    ----------
    cfg:
        EvalConfig loaded from eval_config.yaml.
    """

    def __init__(self, cfg: EvalConfig) -> None:
        self._cfg = cfg
        self._rng = random.Random(cfg.random_seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self) -> Dict[str, Any]:
        """Run evaluation over the configured test set.

        Returns
        -------
        Dict with aggregate metrics.  Always printed and optionally saved
        to output_dir/results.json.
        """
        test_set = self._load_testset()
        if not test_set:
            print("[AstroPlanEvaluator] Empty test set — nothing to evaluate.")
            return {}

        # Sub-sample if requested
        n = max(1, int(len(test_set) * self._cfg.eval_portion_pct / 100))
        if n < len(test_set):
            test_set = self._rng.sample(test_set, n)

        print(
            f"[AstroPlanEvaluator] dataset={self._cfg.dataset_type}  "
            f"tasks={len(test_set)}  backend={self._cfg.backend.get('backend', '?')}"
        )

        adapter = self._make_adapter()
        llm_backend = self._make_llm_backend()

        results: List[EvalResult] = []
        for i, task_d in enumerate(test_set):
            print(f"\n--- Task {i + 1}/{len(test_set)} ---")
            result = self._run_task(task_d, adapter, llm_backend)
            results.append(result)
            self._print_task_result(result)

        metrics = self._aggregate(results)
        self._save(metrics, results)
        print("\n[AstroPlanEvaluator] Final metrics:")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
        return metrics

    # ------------------------------------------------------------------
    # Task runner
    # ------------------------------------------------------------------

    def _run_task(
        self,
        task_d: Dict[str, Any],
        adapter: Any,
        llm_backend: Any,
    ) -> EvalResult:
        """Run one task end-to-end.  Returns per-task metrics."""
        task_id = task_d.get("task_id", task_d.get("task", "?"))
        nl_inst = self._extract_nl_inst(task_d)
        t0 = time.time()

        # Reset environment
        try:
            init_obs = adapter.reset(task_d)
        except Exception as exc:
            print(f"  [reset error] {exc}")
            return EvalResult(
                task_id=task_id, nl_inst=nl_inst, success=False,
                goal_success_rate=0.0, subgoal_success_rate=0.0,
                replan_count=0, tree_max_depth=0,
                terminate_reason=f"env_reset_error:{exc}",
            )

        # Wire components
        bridge = EnvMCPBridge(adapter)
        interlock = PassthroughInterlock()

        # Build LaboratoryEnvironment
        env = self._make_env(llm_backend, bridge, interlock, init_obs)

        # Run the agent tree (asyncio.run is safe here — no outer event loop)
        try:
            exec_result = asyncio.run(env.run(nl_inst))
        except Exception as exc:
            print(f"  [run error] {exc}")
            exec_result = None

        tree_success = bool(exec_result and exec_result.status == "completed")

        # Environment-level success check
        success, goal_sr, subgoal_sr = self._check_env_success(
            adapter, tree_success
        )

        # Depth from log
        max_depth = self._compute_max_depth(exec_result)
        lineage_ids = self._extract_lineage_ids(env)

        return EvalResult(
            task_id=task_id,
            nl_inst=nl_inst,
            success=success,
            goal_success_rate=goal_sr,
            subgoal_success_rate=subgoal_sr,
            replan_count=0,          # AstroPlan.execute_standalone() tracks this;
                                     # direct env.run() has no replanning.
            tree_max_depth=max_depth,
            lineage_ids_seen=lineage_ids,
            wall_time_s=time.time() - t0,
            terminate_reason="done" if success else "failure",
        )

    # ------------------------------------------------------------------
    # Component factories
    # ------------------------------------------------------------------

    def _make_adapter(self) -> Any:
        """Return the env adapter for the configured dataset_type."""
        dtype = self._cfg.dataset_type
        if dtype == "alfred":
            from src.evaluation.environments.alfred_adapter import AlfredAdapter
            if not AlfredAdapter.available:
                print("[AstroPlanEvaluator] ALFRED not available — using mock adapter.")
                return _MockEnvAdapter()
            return AlfredAdapter(self._cfg.backend)  # cfg reused for env init

        if dtype == "wah":
            from src.evaluation.environments.wah_adapter import WahAdapter
            if not WahAdapter.available:
                print("[AstroPlanEvaluator] WAH-NL not available — using mock adapter.")
                return _MockEnvAdapter()
            return WahAdapter(self._cfg.backend)

        # Unknown type — fall back to mock
        print(f"[AstroPlanEvaluator] Unknown dataset_type '{dtype}' — using mock.")
        return _MockEnvAdapter()

    def _make_llm_backend(self) -> Any:
        from src.cognition.llm_backends import make_backend
        return make_backend(self._cfg.backend)

    def _make_env(
        self,
        llm_backend: Any,
        bridge: EnvMCPBridge,
        interlock: PassthroughInterlock,
        init_obs: str,
    ) -> Any:
        """Build a LaboratoryEnvironment wired for eval (plan_mode=False)."""
        from src.core.environment import LaboratoryEnvironment
        from src.cognition.agent_node import AgentNode
        from src.cognition.control_flow import ControlFlowNode
        from src.cognition.replanner import SubTreeReplanner
        from src.cognition.latency_observer import LatencyObserver
        from src.control.output_controller import OutputController
        from src.memory.working_memory import WorkingMemory
        from src.memory.milestone_engine import MilestoneEngine
        from src.application.ground_command_receiver import GroundCommandReceiver
        from src.application.hitl_operator import HITLSuspensionOperator
        from src.application.web_monitor import WebMonitor
        from src.execution.hardware_executor import HardwareExecutor

        lab_id = f"eval_{self._cfg.dataset_type}"
        mem = WorkingMemory(lab_id=lab_id)
        # Seed the initial observation as a telemetry entry so the agent sees it
        if init_obs:
            mem.update_telemetry({"init_obs": init_obs})

        agent_node = AgentNode(node_id="root", llm_client=llm_backend)
        cf_node = ControlFlowNode(control_type="Sequence")
        replanner = SubTreeReplanner(max_depth=1, agent_node=agent_node)
        latency_obs = LatencyObserver(threshold_ms=99_999)
        hw = HardwareExecutor(bandwidth_kbps=10_000, lab_id=lab_id)
        output_ctrl = OutputController(compress=False)
        milestone_engine = MilestoneEngine()
        gcr = GroundCommandReceiver()
        hitl = HITLSuspensionOperator(timeout_s=3600)
        monitor = WebMonitor(host="0.0.0.0", port=8765, enabled=False)

        return LaboratoryEnvironment(
            lab_id=lab_id,
            interlock_engine=interlock,
            working_memory=mem,
            agent_node=agent_node,
            control_flow_node=cf_node,
            replanner=replanner,
            latency_observer=latency_obs,
            hardware_executor=hw,
            output_controller=output_ctrl,
            milestone_engine=milestone_engine,
            ground_cmd_receiver=gcr,
            hitl_operator=hitl,
            web_monitor=monitor,
            mcp_registry=bridge,
            max_depth=self._cfg.max_depth,
            plan_mode=False,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_testset(self) -> List[Dict[str, Any]]:
        path = self._cfg.testset_path
        if not path or not pathlib.Path(path).exists():
            print(f"[AstroPlanEvaluator] testset_path '{path}' not found.")
            return []
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Handle both list and dict-keyed test sets
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return list(data.values()) if isinstance(data, dict) else []

    def _extract_nl_inst(self, task_d: Dict[str, Any]) -> str:
        """Extract natural-language instruction from task dict."""
        # WAH-NL uses nl_instructions list; ALFRED uses task key
        if "nl_instructions" in task_d:
            insts = task_d["nl_instructions"]
            return insts[0] if insts else ""
        return task_d.get("task", task_d.get("nl_inst", "complete task"))

    def _check_env_success(
        self, adapter: Any, tree_success: bool
    ) -> Tuple[bool, float, float]:
        """Return (success, goal_sr, subgoal_sr) from the env adapter."""
        dtype = self._cfg.dataset_type

        if isinstance(adapter, _MockEnvAdapter):
            return tree_success, float(tree_success), float(tree_success)

        if dtype == "alfred":
            ok = adapter.check_success()
            return ok, float(ok), float(ok)

        if dtype == "wah":
            goal_sr, subgoal_sr = adapter.check_success()
            return goal_sr == 1.0, goal_sr, subgoal_sr

        return tree_success, float(tree_success), float(tree_success)

    def _compute_max_depth(self, exec_result: Any) -> int:
        """Derive tree max_depth from expand events in the execution log."""
        if exec_result is None or not exec_result.execution_log:
            return 0
        depths = [
            e.get("depth", 0)
            for e in exec_result.execution_log
            if e.get("type") == "expand"
        ]
        return max(depths, default=0)

    def _extract_lineage_ids(self, env: Any) -> List[str]:
        """Collect lineage_ids from the DAG built during this task run."""
        try:
            return [
                pn.lineage_id
                for pn in env._dag._plan_nodes.values()
            ]
        except AttributeError:
            return []

    def _aggregate(self, results: List[EvalResult]) -> Dict[str, Any]:
        """Compute aggregate metrics matching ReAcTree's reported values."""
        n = len(results)
        if n == 0:
            return {}

        success_rate = sum(r.success for r in results) / n * 100
        avg_goal_sr = sum(r.goal_success_rate for r in results) / n
        avg_subgoal_sr = sum(r.subgoal_success_rate for r in results) / n
        avg_replan = sum(r.replan_count for r in results) / n
        avg_depth = sum(r.tree_max_depth for r in results) / n
        avg_time = sum(r.wall_time_s for r in results) / n

        # lineage_stability: fraction of tasks where all lineage_ids are non-empty
        n_stable = sum(
            1 for r in results
            if r.lineage_ids_seen and all(lid for lid in r.lineage_ids_seen)
        )
        lineage_stability = n_stable / n if n else 0.0

        metrics: Dict[str, Any] = {
            "total_tasks": n,
            "n_success": sum(r.success for r in results),
            "success_rate_pct": round(success_rate, 2),
            "avg_goal_success_rate": round(avg_goal_sr, 4),
            "avg_subgoal_success_rate": round(avg_subgoal_sr, 4),
            "avg_replan_count": round(avg_replan, 3),
            "avg_tree_max_depth": round(avg_depth, 2),
            "lineage_stability": round(lineage_stability, 4),
            "avg_wall_time_s": round(avg_time, 2),
        }
        return metrics

    def _save(
        self, metrics: Dict[str, Any], results: List[EvalResult]
    ) -> None:
        """Write metrics + per-task results to output_dir."""
        out = pathlib.Path(self._cfg.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        with open(out / "metrics.json", "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2, ensure_ascii=False)

        rows = [
            {
                "task_id": r.task_id,
                "nl_inst": r.nl_inst,
                "success": r.success,
                "goal_success_rate": r.goal_success_rate,
                "subgoal_success_rate": r.subgoal_success_rate,
                "replan_count": r.replan_count,
                "tree_max_depth": r.tree_max_depth,
                "wall_time_s": round(r.wall_time_s, 2),
                "terminate_reason": r.terminate_reason,
            }
            for r in results
        ]
        with open(out / "per_task.json", "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)

        print(f"[AstroPlanEvaluator] Results saved to {out}/")

    @staticmethod
    def _print_task_result(r: EvalResult) -> None:
        status = "✓" if r.success else "✗"
        print(
            f"  {status} task={r.task_id}  "
            f"goal_sr={r.goal_success_rate:.2f}  "
            f"subgoal_sr={r.subgoal_success_rate:.2f}  "
            f"depth={r.tree_max_depth}  "
            f"time={r.wall_time_s:.1f}s"
        )


# ---------------------------------------------------------------------------
# MockEnvAdapter — fallback when simulators are unavailable
# ---------------------------------------------------------------------------

class _MockEnvAdapter:
    """Minimal env adapter that returns canned responses.

    Used when ALFRED / WAH simulators are not installed so the evaluator
    can be exercised in CI / development without full dependencies.
    """

    def reset(self, task_d: Dict[str, Any]) -> str:
        return "Mock environment ready."

    def step(self, nl_skill: str) -> Dict[str, Any]:
        return {"text": f"Mock: executed '{nl_skill}'", "success": True}

    def get_skill_set(self) -> List[str]:
        return []

    def check_success(self) -> Any:
        return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AstroPlan Evaluator")
    parser.add_argument(
        "--config", default="config/eval_config.yaml",
        help="Path to eval_config.yaml"
    )
    args = parser.parse_args()

    cfg = load_eval_config(args.config)
    evaluator = AstroPlanEvaluator(cfg)
    evaluator.evaluate()
