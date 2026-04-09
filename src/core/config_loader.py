"""Config loader — reads config.yaml and resolves env-var substitutions."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


_ENV_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: Any) -> Any:
    """Recursively replace ${ENV_VAR} placeholders in strings."""
    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(0))
        return _ENV_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


@dataclass
class LLMConfig:
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    max_tokens: int = 2048
    temperature: float = 0.2
    use_mock: bool = True
    # Local-inference fields (used when backend == "hf_local")
    backend: str = "anthropic"   # "anthropic" | "hf_local" | "mock"
    model_path: str = ""         # HF model ID or local directory path
    device: str = "auto"         # "auto" | "cuda" | "cpu"
    load_in_4bit: bool = False
    load_in_8bit: bool = False


@dataclass
class MCPConfig:
    compress: bool = True


@dataclass
class OrchestratorConfig:
    max_replan_depth: int = 3
    hitl_timeout_s: int = 300
    latency_threshold_ms: int = 5000


@dataclass
class WebMonitorConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    enabled: bool = False


@dataclass
class AppConfig:
    lab_id: str = "Fluid-Lab-Demo"
    bandwidth_kbps: int = 200
    llm: LLMConfig = field(default_factory=LLMConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    web_monitor: WebMonitorConfig = field(default_factory=WebMonitorConfig)


def load_config(path: str = "./config/config.yaml") -> AppConfig:
    """Load and parse config.yaml into a typed AppConfig."""
    if yaml is None:
        raise ImportError("PyYAML is required: pip install pyyaml")

    with open(path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    raw = _resolve_env(raw)

    llm_d = raw.get("llm", {})
    mcp_d = raw.get("mcp", {})
    orch_d = raw.get("orchestrator", {})
    wm_d = raw.get("web_monitor", {})

    return AppConfig(
        lab_id=raw.get("lab_id", "Fluid-Lab-Demo"),
        bandwidth_kbps=raw.get("bandwidth_kbps", 200),
        llm=LLMConfig(
            model=llm_d.get("model", "claude-sonnet-4-6"),
            api_key=llm_d.get("api_key", ""),
            max_tokens=llm_d.get("max_tokens", 2048),
            temperature=llm_d.get("temperature", 0.2),
            use_mock=llm_d.get("use_mock", True),
            backend=llm_d.get("backend", "anthropic"),
            model_path=llm_d.get("model_path", ""),
            device=llm_d.get("device", "auto"),
            load_in_4bit=llm_d.get("load_in_4bit", False),
            load_in_8bit=llm_d.get("load_in_8bit", False),
        ),
        mcp=MCPConfig(compress=mcp_d.get("compress", True)),
        orchestrator=OrchestratorConfig(
            max_replan_depth=orch_d.get("max_replan_depth", 3),
            hitl_timeout_s=orch_d.get("hitl_timeout_s", 300),
            latency_threshold_ms=orch_d.get("latency_threshold_ms", 5000),
        ),
        web_monitor=WebMonitorConfig(
            host=wm_d.get("host", "0.0.0.0"),
            port=wm_d.get("port", 8765),
            enabled=wm_d.get("enabled", False),
        ),
    )
