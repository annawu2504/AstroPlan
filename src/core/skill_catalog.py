"""SkillCatalog — loads config/skills.yaml and registers MCP skill implementations.

The catalog is the single source of truth for what skills exist, what they do
(shown to the LLM), and how they execute (side-effects on memory/interlock/telemetry).

Usage::

    catalog = SkillCatalog.load("config/skills.yaml")
    catalog.register_all(registry, memory, interlock, telemetry_bus)

    # In the planner prompt:
    print(catalog.skill_list_for_prompt())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass
class SkillEntry:
    name: str
    description: str
    subsystem: str
    subsystem_state: Optional[str]       # FSM state after execution (None = no change)
    fsm_action: Optional[str]            # Action key passed to InterlockEngine
    telemetry_effects: Dict[str, Any] = field(default_factory=dict)
    result_fields: Dict[str, Any] = field(default_factory=dict)


class SkillCatalog:
    """Loads skill definitions from YAML and registers executable implementations."""

    def __init__(self, entries: List[SkillEntry]) -> None:
        self._entries = entries
        self._by_name: Dict[str, SkillEntry] = {e.name: e for e in entries}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str = "config/skills.yaml") -> "SkillCatalog":
        """Parse a skills.yaml file and return a SkillCatalog instance."""
        if yaml is None:
            raise ImportError("PyYAML is required: pip install pyyaml")

        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

        entries: List[SkillEntry] = []
        for item in raw.get("skills", []):
            entries.append(SkillEntry(
                name=item["name"],
                description=str(item.get("description", "")).strip(),
                subsystem=item.get("subsystem", ""),
                subsystem_state=item.get("subsystem_state"),
                fsm_action=item.get("fsm_action"),
                telemetry_effects=item.get("telemetry_effects") or {},
                result_fields=item.get("result_fields") or {},
            ))
        return cls(entries)

    # ------------------------------------------------------------------
    # LLM context
    # ------------------------------------------------------------------

    def skill_list_for_prompt(self) -> str:
        """Return a formatted multi-line string listing all skills for LLM prompts."""
        lines: List[str] = []
        for e in self._entries:
            # Collapse multi-line descriptions to a single sentence
            one_liner = " ".join(e.description.split())
            lines.append(f"  - {e.name}: {one_liner}")
        return "\n".join(lines)

    def skill_descriptions(self) -> Dict[str, str]:
        """Return {name: description} for all skills."""
        return {e.name: " ".join(e.description.split()) for e in self._entries}

    def skill_names(self) -> List[str]:
        return [e.name for e in self._entries]

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_all(
        self,
        registry: Any,
        memory: Any,
        interlock: Any,
        telemetry_bus: Any,
    ) -> None:
        """Build and register a callable implementation for every skill entry.

        Also stores the description in the registry so it is accessible to the
        planner for LLM prompt construction.
        """
        for entry in self._entries:
            impl = self._build_impl(entry, memory, interlock, telemetry_bus)
            registry.register(entry.name, impl)
            registry.set_description(entry.name, entry.description)

    # ------------------------------------------------------------------
    # Implementation factory
    # ------------------------------------------------------------------

    def _build_impl(
        self,
        entry: SkillEntry,
        memory: Any,
        interlock: Any,
        telemetry_bus: Any,
    ):
        """Return a callable (params: dict) -> dict for the given skill entry.

        Generic skills are built from the YAML fields.  Special-case skills
        (read_telemetry, emergency_stop) have custom logic.
        """
        name = entry.name

        if name == "read_telemetry":
            return self._impl_read_telemetry(memory)

        if name == "emergency_stop":
            return self._impl_emergency_stop(memory, interlock, telemetry_bus)

        if name == "read_tension_sensor":
            return self._impl_read_sensor(memory, "tension_n", default=0.0)

        if name == "emergency_stop_print":
            return self._impl_emergency_stop_print(memory, interlock, telemetry_bus)

        # Generic: apply FSM action, update subsystem state, update telemetry
        return self._impl_generic(entry, memory, interlock, telemetry_bus)

    # ------ generic implementation -------------------------------------

    @staticmethod
    def _impl_generic(
        entry: SkillEntry,
        memory: Any,
        interlock: Any,
        telemetry_bus: Any,
    ):
        fsm_action = entry.fsm_action
        subsystem = entry.subsystem
        subsystem_state = entry.subsystem_state
        telemetry_effects = dict(entry.telemetry_effects)
        result_fields = dict(entry.result_fields)

        def _execute(params: dict) -> dict:
            if fsm_action:
                interlock.apply_action(fsm_action)
            if subsystem and subsystem_state:
                memory.update_subsystem_state(subsystem, subsystem_state)
            if telemetry_effects:
                telemetry_bus.apply_mock_update(telemetry_effects)
                memory.update_telemetry(telemetry_effects)
            result = {"status": "ok"}
            result.update(result_fields)
            return result

        _execute.__name__ = entry.name
        return _execute

    # ------ special implementations ------------------------------------

    @staticmethod
    def _impl_read_telemetry(memory: Any):
        def read_telemetry(params: dict) -> dict:
            snapshot = memory.snapshot()
            return {"status": "ok", "telemetry": snapshot.telemetry}
        return read_telemetry

    @staticmethod
    def _impl_emergency_stop(memory: Any, interlock: Any, telemetry_bus: Any):
        def emergency_stop(params: dict) -> dict:
            snapshot = memory.snapshot()
            states = snapshot.subsystem_states

            # Safe shutdown order: camera → thermal → pump
            if states.get("camera") == "ACTIVE":
                try:
                    interlock.apply_action("deactivate_camera")
                    memory.update_subsystem_state("camera", "IDLE")
                except Exception:
                    pass

            if states.get("thermal") in ("HEATING", "TARGET_REACHED"):
                try:
                    interlock.apply_action("cool_down")
                    memory.update_subsystem_state("thermal", "IDLE")
                except Exception:
                    pass

            if states.get("fluid_pump") == "ACTIVE":
                try:
                    interlock.apply_action("deactivate_pump")
                    memory.update_subsystem_state("fluid_pump", "IDLE")
                except Exception:
                    pass

            effects = {"flow_rate": 0.0, "temperature": 22.0, "camera_status": "OFF"}
            telemetry_bus.apply_mock_update(effects)
            memory.update_telemetry(effects)
            return {"status": "ok", "stopped": True}

        return emergency_stop

    @staticmethod
    def _impl_read_sensor(memory: Any, telemetry_key: str, default: float = 0.0):
        """Generic sensor read — returns current telemetry value for a key."""
        def read_sensor(params: dict) -> dict:
            snapshot = memory.snapshot()
            value = snapshot.telemetry.get(telemetry_key, default)
            return {"status": "ok", telemetry_key: value}
        return read_sensor

    @staticmethod
    def _impl_emergency_stop_print(memory: Any, interlock: Any, telemetry_bus: Any):
        """Emergency stop for fiber composite print lab.

        Aborts active print job, releases fiber tension, holds nozzle at
        200°C standby, keeps vacuum running for debris containment.
        """
        def emergency_stop_print(params: dict) -> dict:
            snapshot = memory.snapshot()
            states = snapshot.subsystem_states

            # Abort print job if active
            if states.get("print_job") in ("FIRST_LAYER", "FORMING"):
                try:
                    interlock.apply_action("abort_print")
                except Exception:
                    pass

            # Release fiber tension
            if states.get("fiber_tension") == "STABLE":
                try:
                    interlock.apply_action("release_tension")
                except Exception:
                    pass

            # Set nozzle to safe standby temp; vacuum stays RUNNING
            effects = {"nozzle_temp_c": 200.0, "tension_n": 0.0}
            telemetry_bus.apply_mock_update(effects)
            memory.update_telemetry(effects)
            return {"status": "ok", "stopped": True}

        return emergency_stop_print
