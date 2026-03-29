"""MCPRegistry — Model Context Protocol tool registry.

Provides a decorator-based API for registering named skills that the
AgentNode can invoke.  Optionally compresses tool payloads to respect
SpaceWire bandwidth constraints.
"""
from __future__ import annotations

import functools
import zlib
from typing import Any, Callable, Dict, Optional


class MCPRegistry:
    """Central registry for MCP-style callable tools.

    Usage::

        registry = MCPRegistry(compress=True)

        @registry.mcp_tool
        def activate_pump(params: dict) -> dict:
            ...

        result = registry.call("activate_pump", {})
    """

    def __init__(self, compress: bool = True):
        self._compress = compress
        self._tools: Dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def mcp_tool(self, fn: Callable) -> Callable:
        """Decorator that registers *fn* as an MCP tool under its function name."""
        self._tools[fn.__name__] = fn

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return wrapper

    def register(self, name: str, fn: Callable) -> None:
        """Programmatically register a tool under an explicit *name*."""
        self._tools[name] = fn

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    def call(self, skill: str, params: Dict[str, Any]) -> Any:
        """Invoke a registered skill by name."""
        fn = self._tools.get(skill)
        if fn is None:
            raise KeyError(f"MCPRegistry: unknown skill '{skill}'. "
                           f"Registered: {list(self._tools.keys())}")
        return fn(params)

    def has_skill(self, skill: str) -> bool:
        return skill in self._tools

    def skill_names(self) -> list:
        return list(self._tools.keys())
