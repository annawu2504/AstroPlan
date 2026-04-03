"""AstroPlan — entry point.

Runs a Fluid-Lab-Demo mission using the mock planner (no LLM API key
required).  Set ANTHROPIC_API_KEY in the environment to activate the
real LLM planning path.

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
from src.execution.hardware_executor import HardwareExecutor
from src.memory.working_memory import WorkingMemory
from src.memory.milestone_engine import MilestoneEngine
from src.control.output_controller import OutputController
from src.cognition.agent_node import AgentNode
from src.cognition.control_flow import ControlFlowNode
from src.cognition.replanner import SubTreeReplanner
from src.cognition.latency_observer import LatencyObserver
from src.application.ground_command_receiver import GroundCommandReceiver
from src.application.hitl_operator import HITLSuspensionOperator
from src.application.web_monitor import WebMonitor
from src.core.mcp_registry import MCPRegistry
from src.core.environment import LaboratoryEnvironment


def _make_llm_client(cfg):
    """Return an LLM client if ANTHROPIC_API_KEY is set, else None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
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
        print("[main] anthropic package not installed — using mock planner.")
        return None


def _register_demo_skills(
    registry: MCPRegistry,
    memory: WorkingMemory,
    interlock: InterlockEngine,
    telemetry_bus: TelemetryBus,
) -> None:
    """Register Fluid-Lab-Demo skills in the MCP registry."""

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
    bandwidth: int = cfg.bandwidth_kbps

    # --- Layer 1: Environment & Execution ---
    ingestor = TaskDataset.parse_requirements(
        "进行流体实验：激活泵，加热至40°C，启动摄像头记录数据。"
    )
    nl_goal = ingestor.nl_global_goal

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

    hardware_executor = HardwareExecutor(
        bandwidth_kbps=bandwidth, lab_id=lab_id
    )

    # --- Layer 2: Physics ---
    interlock = InterlockEngine.from_yaml("config/fsm_rules.yaml", lab_id=lab_id)

    # --- Layer 3: Memory & Output ---
    working_memory = WorkingMemory(lab_id=lab_id)
    working_memory.update_telemetry(
        {"temperature": 20.0, "flow_rate": 0.0, "camera_status": "OFF"}
    )
    for subsystem in ("fluid_pump", "thermal", "camera"):
        working_memory.update_subsystem_state(subsystem, "IDLE")

    milestone_engine = MilestoneEngine()
    milestone_engine.build_index(
        [
            {
                "goal": "流体实验",
                "trajectory": [
                    {"skill": "activate_pump", "params": {}},
                    {"skill": "heat_to_40", "params": {}},
                    {"skill": "activate_camera", "params": {}},
                ],
            }
        ]
    )

    output_controller = OutputController(
        compress=cfg.mcp.compress
    )

    # --- Layer 4: Cognition ---
    llm_client = _make_llm_client(cfg)
    agent_node = AgentNode(node_id="root", llm_client=llm_client)
    control_flow_node = ControlFlowNode(control_type="Sequence")
    replanner = SubTreeReplanner(
        max_depth=cfg.orchestrator.max_replan_depth,
        agent_node=agent_node,
    )
    latency_observer = LatencyObserver(
        threshold_ms=cfg.orchestrator.latency_threshold_ms,
    )

    # --- Layer 5: Application ---
    gcr = GroundCommandReceiver()
    hitl = HITLSuspensionOperator(
        timeout_s=cfg.orchestrator.hitl_timeout_s
    )
    monitor = WebMonitor(
        host=cfg.web_monitor.host,
        port=cfg.web_monitor.port,
        enabled=cfg.web_monitor.enabled,
    )

    # --- MCP skill registration ---
    mcp = MCPRegistry(compress=cfg.mcp.compress)
    _register_demo_skills(mcp, working_memory, interlock, telemetry_bus)

    # --- Orchestrator ---
    env = LaboratoryEnvironment(
        lab_id=lab_id,
        interlock_engine=interlock,
        working_memory=working_memory,
        agent_node=agent_node,
        control_flow_node=control_flow_node,
        replanner=replanner,
        latency_observer=latency_observer,
        hardware_executor=hardware_executor,
        output_controller=output_controller,
        milestone_engine=milestone_engine,
        ground_cmd_receiver=gcr,
        hitl_operator=hitl,
        web_monitor=monitor,
        mcp_registry=mcp,
    )

    result = await env.run(nl_goal)
    print(f"执行结果: {{'status': '{result.status}', 'total_steps': {result.total_steps}, 'execution_log': [...]}}")


if __name__ == "__main__":
    asyncio.run(main())
