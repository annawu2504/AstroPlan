"""Standalone evaluation utilities for AstroPlan.

MockScheduler
    Drives the plan–execute–replan loop without a live agentos_scheduler.
    Supports synthetic failure injection for replan stress-testing.

AstroPlanEvaluator
    End-to-end evaluation runner for ALFRED and WAH-NL benchmarks.
    Supports Ollama, HuggingFace local, and Anthropic LLM backends.

EvalConfig / load_eval_config
    Typed config and YAML loader for config/eval_config.yaml.

Quick start::

    # DAG-level smoke test (no simulator)
    from src.evaluation import MockScheduler
    scheduler = MockScheduler(registry, failure_rate=0.2, seed=42)
    result = await planner.execute_standalone(mission, scheduler=scheduler)

    # Full benchmark (WAH-NL + Ollama)
    from src.evaluation import AstroPlanEvaluator, load_eval_config
    cfg = load_eval_config("config/eval_config.yaml")
    metrics = AstroPlanEvaluator(cfg).evaluate()
"""
from src.evaluation.mock_scheduler import MockScheduler
from src.evaluation.evaluator import AstroPlanEvaluator, EvalConfig, load_eval_config
from src.evaluation.task_suite import (
    TaskDefinition,
    TaskMetrics,
    BenchmarkReport,
    TaskSuite,
    SpaceLabBenchmark,
)

__all__ = [
    "MockScheduler",
    "AstroPlanEvaluator",
    "EvalConfig",
    "load_eval_config",
    "TaskDefinition",
    "TaskMetrics",
    "BenchmarkReport",
    "TaskSuite",
    "SpaceLabBenchmark",
]
