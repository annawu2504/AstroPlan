"""SpaceLabBenchmark — space-lab-native evaluation harness for AstroPlan.

Produces ALFRED/WAH-compatible metrics from the three real experiment labs:
  - Fluid-Lab-Demo
  - fiber-composite-lab
  - microbio-sampling-lab

Metrics align with ReAcTree / ALFRED reporting:

  task_success_rate   (TS)  — fraction of tasks where ALL goal conditions met
  goal_condition_rate (GC)  — average fraction of goal conditions met per task
  skill_f1                  — token-level F1 between executed and gold skill list
  efficiency                — len(gold) / len(executed), capped at 1.0
  avg_replan_count          — mean replanning iterations per task

Usage::

    from src.evaluation.task_suite import TaskSuite, SpaceLabBenchmark

    # Run all tasks for a lab
    suite  = TaskSuite.load("config/tasks/fiber_composite_tasks.yaml")
    report = await SpaceLabBenchmark().run_suite(suite, failure_rate=0.0)
    print(report.summary())

    # Run a single task
    task   = suite.tasks[0]
    metric = await SpaceLabBenchmark().run_task(task)
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

@dataclass
class TaskDefinition:
    """A single evaluation task.

    Attributes
    ----------
    task_id:
        Unique identifier (e.g. "fiber_003").
    lab_id:
        Which lab configuration to use (maps to config/labs/{lab_id}/).
    nl_goal:
        Natural-language mission string (Chinese) — given to the planner.
    nl_goal_en:
        English translation (used in reports).
    expected_skills:
        Ordered gold-plan skill sequence for F1/efficiency scoring.
    goal_conditions:
        Terminal subsystem states that must be reached for success
        (``{subsystem: state}``).  Empty dict = success if execution
        completes without error.
    difficulty:
        ``"easy"`` | ``"medium"`` | ``"hard"``
    tags:
        Free-form labels for grouping / filtering.
    """
    task_id: str
    lab_id: str
    nl_goal: str
    nl_goal_en: str = ""
    expected_skills: List[str] = field(default_factory=list)
    goal_conditions: Dict[str, str] = field(default_factory=dict)
    difficulty: str = "medium"
    tags: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-task metrics
# ---------------------------------------------------------------------------

@dataclass
class TaskMetrics:
    """Evaluation result for a single task.

    Matches ALFRED / WAH-NL reporting conventions where applicable.
    """
    task_id: str
    success: bool                     # all goal_conditions satisfied
    goal_condition_rate: float        # GC — fraction of goal conditions met
    skill_f1: float                   # F1 between executed vs gold skills
    total_steps: int                  # actual MCP skill calls executed
    optimal_steps: int                # len(expected_skills)
    efficiency: float                 # optimal / actual (≤ 1.0)
    replan_count: int                 # number of replan iterations
    executed_skills: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Benchmark report
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkReport:
    """Aggregate metrics over a full task suite run."""
    lab_id: str
    n_tasks: int
    task_success_rate: float     # ALFRED TS
    goal_condition_rate: float   # ALFRED GC
    avg_skill_f1: float
    avg_efficiency: float
    avg_replan_count: float
    per_task: List[TaskMetrics] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Lab: {self.lab_id}   Tasks: {self.n_tasks}",
            f"  Task Success Rate  : {self.task_success_rate * 100:.1f}%",
            f"  Goal Condition Rate: {self.goal_condition_rate * 100:.1f}%",
            f"  Skill Plan F1      : {self.avg_skill_f1 * 100:.1f}%",
            f"  Efficiency         : {self.avg_efficiency * 100:.1f}%",
            f"  Avg Replans        : {self.avg_replan_count:.2f}",
            "  Per-task:",
        ]
        for m in self.per_task:
            status = "✓" if m.success else "✗"
            lines.append(
                f"    {status} {m.task_id:20s}  "
                f"GC={m.goal_condition_rate:.2f}  "
                f"F1={m.skill_f1:.2f}  "
                f"eff={m.efficiency:.2f}  "
                f"steps={m.total_steps}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task suite loader
# ---------------------------------------------------------------------------

class TaskSuite:
    """Loads task definitions from a YAML file."""

    def __init__(self, tasks: List[TaskDefinition], lab_id: str) -> None:
        self.tasks = tasks
        self.lab_id = lab_id

    @classmethod
    def load(cls, path: str) -> "TaskSuite":
        if yaml is None:
            raise ImportError("PyYAML is required: pip install pyyaml")
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        lab_id = raw.get("lab_id", "unknown")
        tasks: List[TaskDefinition] = []
        for item in raw.get("tasks", []):
            tasks.append(TaskDefinition(
                task_id=item["task_id"],
                lab_id=lab_id,
                nl_goal=item["nl_goal"],
                nl_goal_en=item.get("nl_goal_en", ""),
                expected_skills=item.get("expected_skills", []),
                goal_conditions=item.get("goal_conditions") or {},
                difficulty=item.get("difficulty", "medium"),
                tags=item.get("tags", []),
            ))
        return cls(tasks, lab_id)

    @classmethod
    def load_all(cls, tasks_dir: str = "config/tasks") -> List["TaskSuite"]:
        """Load every *.yaml file in tasks_dir, return list of TaskSuites."""
        suites: List["TaskSuite"] = []
        for fname in os.listdir(tasks_dir):
            if fname.endswith(".yaml") or fname.endswith(".yml"):
                suites.append(cls.load(os.path.join(tasks_dir, fname)))
        return suites

    def by_difficulty(self, difficulty: str) -> List[TaskDefinition]:
        return [t for t in self.tasks if t.difficulty == difficulty]

    def by_tag(self, tag: str) -> List[TaskDefinition]:
        return [t for t in self.tasks if tag in t.tags]


# ---------------------------------------------------------------------------
# SpaceLabBenchmark
# ---------------------------------------------------------------------------

class SpaceLabBenchmark:
    """ALFRED/WAH-compatible evaluation harness for AstroPlan space labs.

    Creates a fresh AstroPlan environment for each task (avoids state leakage
    between tasks). Uses ``execute_standalone()`` + MockScheduler so the full
    plan–execute–replan loop is exercised.

    Parameters
    ----------
    config_yaml:
        Path to config.yaml.  The lab_id inside is overridden per task.
    llm_client:
        Optional LLM client.  ``None`` → rule-based mock planner.
    """

    def __init__(
        self,
        config_yaml: str = "config/config.yaml",
        llm_client: Optional[Any] = None,
    ) -> None:
        self._config_yaml = config_yaml
        self._llm_client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_suite(
        self,
        suite: TaskSuite,
        *,
        failure_rate: float = 0.0,
        seed: Optional[int] = None,
        verbose: bool = True,
    ) -> BenchmarkReport:
        """Run all tasks in a suite and return aggregate metrics."""
        per_task: List[TaskMetrics] = []
        for task in suite.tasks:
            if verbose:
                print(f"\n[Benchmark] {task.task_id} ({task.difficulty}) — {task.nl_goal}")
            metrics = await self.run_task(task, failure_rate=failure_rate, seed=seed)
            per_task.append(metrics)
            if verbose:
                status = "✓" if metrics.success else "✗"
                print(
                    f"  {status}  GC={metrics.goal_condition_rate:.2f}  "
                    f"F1={metrics.skill_f1:.2f}  eff={metrics.efficiency:.2f}  "
                    f"steps={metrics.total_steps}  replans={metrics.replan_count}"
                )

        report = self._aggregate(suite.lab_id, per_task)
        if verbose:
            print("\n" + report.summary())
        return report

    async def run_task(
        self,
        task: TaskDefinition,
        *,
        failure_rate: float = 0.0,
        seed: Optional[int] = None,
    ) -> TaskMetrics:
        """Run a single task end-to-end; return per-task metrics."""
        try:
            return await self._execute_task(task, failure_rate, seed)
        except Exception as exc:
            return TaskMetrics(
                task_id=task.task_id,
                success=False,
                goal_condition_rate=0.0,
                skill_f1=0.0,
                total_steps=0,
                optimal_steps=len(task.expected_skills),
                efficiency=0.0,
                replan_count=0,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_task(
        self,
        task: TaskDefinition,
        failure_rate: float,
        seed: Optional[int],
    ) -> TaskMetrics:
        from src.core.config_loader import load_config, AppConfig
        from src.physics.interlock_engine import InterlockEngine
        from src.execution.telemetry_bus import TelemetryBus
        from src.memory.working_memory import WorkingMemory
        from src.core.mcp_registry import MCPRegistry
        from src.core.skill_catalog import SkillCatalog
        from src.planner import AstroPlan
        from src.evaluation.mock_scheduler import MockScheduler

        # Override lab_id in config for this task
        cfg = load_config(self._config_yaml)
        cfg = _override_lab(cfg, task.lab_id)

        # Fresh interlock engine for this task
        interlock = InterlockEngine.from_yaml(cfg.fsm_rules_path, lab_id=task.lab_id)

        # Fresh working memory
        memory = WorkingMemory(lab_id=task.lab_id)
        for subsystem, state in interlock.current_states().items():
            memory.update_subsystem_state(subsystem, state)

        # Telemetry bus (minimal — skill impls update it)
        telemetry_bus = TelemetryBus(lab_id=task.lab_id, rules={})

        # MCP registry with skill catalog
        mcp = MCPRegistry(compress=False)
        catalog = SkillCatalog.load(cfg.skills_path)
        catalog.register_all(mcp, memory, interlock, telemetry_bus)

        # Planner (fresh instance)
        planner = AstroPlan(cfg, interlock, mcp, llm_client=self._llm_client)

        # MockScheduler tracking executed skills
        scheduler = MockScheduler(mcp, failure_rate=failure_rate, seed=seed)

        # Run
        result = await planner.execute_standalone(task.nl_goal, scheduler=scheduler)

        # Count replans
        replan_count = max(0, len(scheduler.submitted_revisions) - 1)

        # Executed skill names (from scheduler tracking)
        executed = list(scheduler.executed_skill_names)

        # Goal condition check against final interlock states
        final_states = interlock.current_states()
        gc_rate = _goal_condition_rate(task.goal_conditions, final_states)
        success = (result.status == "completed") and (gc_rate == 1.0 or not task.goal_conditions)

        # Skill F1
        f1 = _skill_f1(executed, task.expected_skills)

        # Efficiency
        actual = len(executed) if executed else 1
        optimal = len(task.expected_skills) if task.expected_skills else actual
        efficiency = min(1.0, optimal / actual) if actual > 0 else 0.0

        return TaskMetrics(
            task_id=task.task_id,
            success=success,
            goal_condition_rate=gc_rate,
            skill_f1=f1,
            total_steps=actual,
            optimal_steps=optimal,
            efficiency=efficiency,
            replan_count=replan_count,
            executed_skills=executed,
        )

    @staticmethod
    def _aggregate(lab_id: str, per_task: List[TaskMetrics]) -> BenchmarkReport:
        n = len(per_task)
        if n == 0:
            return BenchmarkReport(lab_id=lab_id, n_tasks=0,
                                   task_success_rate=0.0, goal_condition_rate=0.0,
                                   avg_skill_f1=0.0, avg_efficiency=0.0,
                                   avg_replan_count=0.0, per_task=[])
        return BenchmarkReport(
            lab_id=lab_id,
            n_tasks=n,
            task_success_rate=sum(m.success for m in per_task) / n,
            goal_condition_rate=sum(m.goal_condition_rate for m in per_task) / n,
            avg_skill_f1=sum(m.skill_f1 for m in per_task) / n,
            avg_efficiency=sum(m.efficiency for m in per_task) / n,
            avg_replan_count=sum(m.replan_count for m in per_task) / n,
            per_task=per_task,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _goal_condition_rate(conditions: Dict[str, str], final_states: Dict[str, str]) -> float:
    """Fraction of goal conditions satisfied by final_states."""
    if not conditions:
        return 1.0
    met = sum(1 for k, v in conditions.items() if final_states.get(k) == v)
    return met / len(conditions)


def _skill_f1(executed: List[str], expected: List[str]) -> float:
    """Token-level F1 between executed and expected skill lists (set-based)."""
    if not expected and not executed:
        return 1.0
    if not expected or not executed:
        return 0.0
    exec_set = set(executed)
    exp_set = set(expected)
    tp = len(exec_set & exp_set)
    precision = tp / len(exec_set) if exec_set else 0.0
    recall = tp / len(exp_set) if exp_set else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _override_lab(cfg: Any, lab_id: str) -> Any:
    """Return a copy of cfg with lab_id and resolved lab paths."""
    import copy
    from src.core.config_loader import _resolve_lab_paths
    c = copy.copy(cfg)
    c.lab_id = lab_id
    c.fsm_rules_path, c.skills_path = _resolve_lab_paths(lab_id)
    return c
