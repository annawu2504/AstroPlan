"""AstroPlan — standalone entry point.

Demonstrates the real plan → MockScheduler execute loop:

  1. AstroPlan.plan()           generates a PlanResponse DAG (dry-run)
  2. MockScheduler.submit_plan() accepts the DAG
  3. MockScheduler.await_terminal_event() executes each node via MCPRegistry
  4. On failure → AstroPlan.plan() replans; loop repeats

Set ANTHROPIC_API_KEY to enable real LLM planning; otherwise the built-in
rule-based fallback in AgentNode is used (no external services required).

Usage::

    python main.py
"""
from __future__ import annotations

import asyncio
import os

from src.core.config_loader import load_config
from src.physics.interlock_engine import InterlockEngine
from src.execution.task_ingestor import TaskDataset
from src.execution.telemetry_bus import TelemetryBus
from src.memory.working_memory import WorkingMemory
from src.core.mcp_registry import MCPRegistry
from src.planner import AstroPlan
from src.evaluation import MockScheduler


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


def _register_demo_skills(
    registry: MCPRegistry,
    memory: WorkingMemory,
    interlock: InterlockEngine,
    telemetry_bus: TelemetryBus,
) -> None:
    """Register Fluid-Lab-Demo skills in the MCP registry.

    These skills are invoked by MockScheduler when it executes plan nodes.
    Side-effects (FSM transitions, telemetry updates) happen here, not
    during planning (plan_mode=True skips all dispatch).
    """

    @registry.mcp_tool
    def activate_pump(params: dict) -> dict:
        interlock.apply_action("activate_pump")
        memory.update_subsystem_state("fluid_pump", "ACTIVE")
        telemetry_bus.apply_mock_update({"flow_rate": 15.0})
        memory.update_telemetry({"flow_rate": 15.0})
        return {"status": "ok", "flow_rate": 15.0}

    @registry.mcp_tool
    def heat_to_40(params: dict) -> dict:
        interlock.apply_action("heat_to_40")
        memory.update_subsystem_state("thermal", "HEATING")
        telemetry_bus.apply_mock_update({"temperature": 40.0})
        memory.update_telemetry({"temperature": 40.0})
        return {"status": "ok", "temperature": 40.0}

    @registry.mcp_tool
    def activate_camera(params: dict) -> dict:
        interlock.apply_action("activate_camera")
        memory.update_subsystem_state("camera", "ACTIVE")
        telemetry_bus.apply_mock_update({"camera_status": "RECORDING"})
        memory.update_telemetry({"camera_status": "RECORDING"})
        return {"status": "ok", "camera_status": "RECORDING"}


async def main() -> None:
    cfg = load_config()
    lab_id: str = cfg.lab_id

    # --- Task ---
    ingestor = TaskDataset.parse_requirements(
        "进行流体实验：激活泵，加热至40°C，启动摄像头记录数据。"
    )
    nl_goal = ingestor.nl_global_goal

    # --- Telemetry bus (for skill side-effects) ---
    telemetry_bus = TelemetryBus(
        lab_id=lab_id,
        rules={
            "temperature": {"max": 80.0},
            "flow_rate": {"max": 50.0},
            "pressure_kpa": {"max": 200.0},
        },
    )
    telemetry_bus.apply_mock_update(
        {"temperature": 20.0, "flow_rate": 0.0, "camera_status": "OFF"}
    )

    # --- Physics ---
    interlock = InterlockEngine.from_yaml("config/fsm_rules.yaml", lab_id=lab_id)

    # --- Shared working memory (used by skill side-effects via registry) ---
    working_memory = WorkingMemory(lab_id=lab_id)
    working_memory.update_telemetry(
        {"temperature": 20.0, "flow_rate": 0.0, "camera_status": "OFF"}
    )
    for subsystem in ("fluid_pump", "thermal", "camera"):
        working_memory.update_subsystem_state(subsystem, "IDLE")

    # --- MCP skill registry ---
    mcp = MCPRegistry(compress=cfg.mcp.compress)
    _register_demo_skills(mcp, working_memory, interlock, telemetry_bus)

    # --- Planner ---
    llm_client = _make_llm_client(cfg)
    planner = AstroPlan(cfg, interlock, mcp, llm_client=llm_client)

    # --- Mock Scheduler (simulates agentos_scheduler execution) ---
    # failure_rate=0.0 → deterministic success; raise to stress-test replanning
    scheduler = MockScheduler(mcp, failure_rate=0.0)

    # --- Run ---
    print(f"\n[{lab_id}] Mission: {nl_goal}\n")
    result = await planner.execute_standalone(nl_goal, scheduler=scheduler)

    print(f"\n[{lab_id}] 执行结果:")
    print(f"  status       : {result.status}")
    print(f"  total_steps  : {result.total_steps}")
    print(f"  revisions    : {scheduler.submitted_revisions}")
    print(f"  nodes run    : {scheduler.total_nodes_executed}")
    print(f"  failures     : {scheduler.total_failures}")


if __name__ == "__main__":
    asyncio.run(main())
