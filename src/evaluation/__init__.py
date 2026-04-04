"""Standalone evaluation utilities for AstroPlan.

Provides a MockScheduler that drives the plan–execute–replan loop without
requiring a live agentos_scheduler instance.  Use this for benchmarking,
development, and CI testing.

    from src.evaluation import MockScheduler

    scheduler = MockScheduler(registry, failure_rate=0.2, seed=42)
    result = await planner.execute_standalone(mission, scheduler=scheduler)
"""
from src.evaluation.mock_scheduler import MockScheduler

__all__ = ["MockScheduler"]
