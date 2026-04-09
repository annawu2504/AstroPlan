"""AstroPlan — standalone entry point.

Demonstrates the real plan → MockScheduler execute loop:

  1. AstroPlan.plan()           generates a PlanResponse DAG (dry-run)
  2. MockScheduler.submit_plan() accepts the DAG
  3. MockScheduler.await_terminal_event() executes each node via MCPRegistry
  4. On failure → AstroPlan.plan() replans; loop repeats

Usage::

    # Default demo (Fluid-Lab-Demo)
    python main.py

    # Specific lab
    python main.py --lab fiber-composite-lab
    python main.py --lab microbio-sampling-lab

    # Run ALFRED/WAH-compatible benchmark for a lab
    python main.py --benchmark --lab fiber-composite-lab
    python main.py --benchmark          # benchmarks all three labs

Set ANTHROPIC_API_KEY or configure config.yaml to enable real LLM planning;
otherwise the built-in rule-based fallback in AgentNode is used.
"""
from __future__ import annotations

import argparse
import asyncio
import os

from src.core.config_loader import load_config


def _make_llm_client(cfg):
    """Return an LLM client based on cfg.llm.backend, or None for mock/rule planner.

    Supported backends
    ------------------
    "mock"      → None  (rule-based AgentNode fallback, no LLM)
    "hf_local"  → HFLocalClient (HuggingFace Transformers, local GPU/CPU)
    "anthropic" → Anthropic API client (requires ANTHROPIC_API_KEY)
    """
    backend = cfg.llm.backend

    # Explicit mock or use_mock flag → no LLM
    if cfg.llm.use_mock or backend == "mock":
        print("[main] LLM backend: mock (rule-based planner)")
        return None

    # ------------------------------------------------------------------ #
    # Local HuggingFace inference                                          #
    # ------------------------------------------------------------------ #
    if backend == "hf_local":
        from src.llm import HFLocalClient
        model_id = cfg.llm.model_path or cfg.llm.model
        return HFLocalClient(
            model_name_or_path=model_id,
            max_new_tokens=cfg.llm.max_tokens,
            device=cfg.llm.device,
            load_in_4bit=cfg.llm.load_in_4bit,
            load_in_8bit=cfg.llm.load_in_8bit,
            temperature=cfg.llm.temperature,
        )

    # ------------------------------------------------------------------ #
    # Anthropic API                                                        #
    # ------------------------------------------------------------------ #
    api_key = cfg.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[main] No ANTHROPIC_API_KEY — using built-in planner.")
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        model = cfg.llm.model
        max_tokens = cfg.llm.max_tokens

        class _Client:
            def call(self, prompt: str) -> str:
                msg = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text

        print(f"[main] LLM client ready: {model}")
        return _Client()
    except ImportError:
        print("[main] anthropic package not installed — using built-in planner.")
        return None




_DEFAULT_MISSION = {
    "Fluid-Lab-Demo":
        "进行流体实验：激活泵，加热至40°C，启动摄像头记录数据。",
    "fiber-composite-lab":
        "执行完整的空间碳纤维复合材料原位成型实验",
    "microbio-sampling-lab":
        "执行完整空气微生物采样实验，包含培养、拍照与样品封装回收",
}

_TASK_FILES = {
    "Fluid-Lab-Demo":       "config/tasks/fluid_lab_tasks.yaml",
    "fiber-composite-lab":  "config/tasks/fiber_composite_tasks.yaml",
    "microbio-sampling-lab": "config/tasks/microbio_sampling_tasks.yaml",
}


async def run_demo(cfg, lab_id: str, nl_goal: str) -> None:
    """Single-mission demo run."""
    from src.physics.interlock_engine import InterlockEngine
    from src.execution.telemetry_bus import TelemetryBus
    from src.memory.working_memory import WorkingMemory
    from src.core.mcp_registry import MCPRegistry
    from src.core.skill_catalog import SkillCatalog
    from src.planner import AstroPlan
    from src.evaluation import MockScheduler

    telemetry_bus = TelemetryBus(lab_id=lab_id, rules={})
    interlock = InterlockEngine.from_yaml(cfg.fsm_rules_path, lab_id=lab_id)

    working_memory = WorkingMemory(lab_id=lab_id)
    for subsystem, state in interlock.current_states().items():
        working_memory.update_subsystem_state(subsystem, state)

    mcp = MCPRegistry(compress=cfg.mcp.compress)
    catalog = SkillCatalog.load(cfg.skills_path)
    catalog.register_all(mcp, working_memory, interlock, telemetry_bus)

    llm_client = _make_llm_client(cfg)
    planner = AstroPlan(cfg, interlock, mcp, llm_client=llm_client)
    scheduler = MockScheduler(mcp, failure_rate=0.0)

    print(f"\n[{lab_id}] Mission: {nl_goal}\n")
    result = await planner.execute_standalone(nl_goal, scheduler=scheduler)

    print(f"\n[{lab_id}] 执行结果:")
    print(f"  status       : {result.status}")
    print(f"  total_steps  : {result.total_steps}")
    print(f"  revisions    : {scheduler.submitted_revisions}")
    print(f"  nodes run    : {scheduler.total_nodes_executed}")
    print(f"  failures     : {scheduler.total_failures}")
    print(f"  skills run   : {scheduler.executed_skill_names}")


async def run_benchmark(cfg, lab_id: str) -> None:
    """ALFRED/WAH-compatible benchmark for one or all labs."""
    from src.evaluation.task_suite import TaskSuite, SpaceLabBenchmark

    labs = list(_TASK_FILES.keys()) if lab_id == "all" else [lab_id]
    benchmark = SpaceLabBenchmark(llm_client=_make_llm_client(cfg))

    for lid in labs:
        task_file = _TASK_FILES.get(lid)
        if not task_file or not os.path.exists(task_file):
            print(f"[Benchmark] No task file for lab '{lid}' — skipping.")
            continue
        suite = TaskSuite.load(task_file)
        print(f"\n{'='*60}")
        print(f"[Benchmark] Lab: {lid}  ({len(suite.tasks)} tasks)")
        print("="*60)
        report = await benchmark.run_suite(suite)
        # Save report
        import json, pathlib
        out = pathlib.Path("outputs/benchmark")
        out.mkdir(parents=True, exist_ok=True)
        with open(out / f"{lid}_report.json", "w", encoding="utf-8") as fh:
            import dataclasses
            json.dump(dataclasses.asdict(report), fh, indent=2, ensure_ascii=False)
        print(f"\n[Benchmark] Report saved: outputs/benchmark/{lid}_report.json")


async def main() -> None:
    parser = argparse.ArgumentParser(description="AstroPlan standalone runner")
    parser.add_argument(
        "--lab",
        default=None,
        help="Lab ID to use (default: value from config.yaml). "
             "Use 'all' with --benchmark to run all labs.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run ALFRED/WAH-compatible task benchmark instead of single demo.",
    )
    parser.add_argument(
        "--mission",
        default=None,
        help="Override the natural-language mission string for demo mode.",
    )
    args = parser.parse_args()

    cfg = load_config()
    lab_id = args.lab or cfg.lab_id

    # Re-resolve paths if lab_id was overridden via --lab
    if args.lab and args.lab != cfg.lab_id:
        from src.core.config_loader import _resolve_lab_paths
        cfg.lab_id = lab_id
        cfg.fsm_rules_path, cfg.skills_path = _resolve_lab_paths(lab_id)

    if args.benchmark:
        await run_benchmark(cfg, lab_id)
    else:
        nl_goal = args.mission or _DEFAULT_MISSION.get(lab_id, "进行实验")
        await run_demo(cfg, lab_id, nl_goal)


if __name__ == "__main__":
    asyncio.run(main())
