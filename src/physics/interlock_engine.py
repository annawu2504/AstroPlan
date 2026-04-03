"""Physical interlock engine — FSM-based safety gate for hardware actions.

Reads config/fsm_rules.yaml to know which state transitions are legal and
which prerequisite subsystem states must be satisfied before a skill
is allowed to execute.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


class InterlockViolation(Exception):
    """Raised when a requested action violates a physical interlock rule."""


class InterlockEngine:
    """Finite-state machine that validates and applies subsystem transitions.

    Usage::

        engine = InterlockEngine.from_yaml("config/fsm_rules.yaml", lab_id="Fluid-Lab-Demo")
        engine.validate_action("heat_to_40")   # raises InterlockViolation if unsafe
        engine.apply_action("activate_pump")
    """

    def __init__(self, subsystems: Dict[str, Any], thresholds: Dict[str, Any], lab_id: str):
        self.lab_id = lab_id
        self._thresholds: Dict[str, Any] = thresholds
        # Build state machines
        self._states: Dict[str, str] = {}
        self._transitions: Dict[str, Dict[str, Any]] = {}  # subsystem -> {action -> rule}

        for name, spec in subsystems.items():
            self._states[name] = spec["initial"]
            self._transitions[name] = {}
            for from_state, actions in spec.get("transitions", {}).items():
                for action, target in actions.items():
                    if isinstance(target, dict):
                        # Extended rule with requires / target
                        self._transitions[name][action] = {
                            "from": from_state,
                            "target": target["target"],
                            "requires": target.get("requires", {}),
                        }
                    else:
                        self._transitions[name][action] = {
                            "from": from_state,
                            "target": target,
                            "requires": {},
                        }

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str, lab_id: str) -> "InterlockEngine":
        if yaml is None:
            raise ImportError("PyYAML is required: pip install pyyaml")
        with open(path, "r", encoding="utf-8") as fh:
            raw: Dict[str, Any] = yaml.safe_load(fh) or {}
        lab_spec = raw.get(lab_id, {})
        return cls(
            subsystems=lab_spec.get("subsystems", {}),
            thresholds=lab_spec.get("thresholds", {}),
            lab_id=lab_id,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def state(self, subsystem: str) -> str:
        """Return current FSM state of a subsystem."""
        return self._states.get(subsystem, "UNKNOWN")

    def validate_action(self, action: str) -> None:
        """Raise InterlockViolation if *action* is not safe to execute right now."""
        for subsystem, rules in self._transitions.items():
            if action not in rules:
                continue
            rule = rules[action]
            current = self._states[subsystem]
            if current != rule["from"]:
                raise InterlockViolation(
                    f"[{self.lab_id}] Action '{action}' requires '{subsystem}' in state "
                    f"'{rule['from']}' but current state is '{current}'"
                )
            for req_subsystem, req_state in rule["requires"].items():
                actual = self._states.get(req_subsystem, "UNKNOWN")
                if actual != req_state:
                    raise InterlockViolation(
                        f"[{self.lab_id}] Action '{action}' requires '{req_subsystem}' in "
                        f"state '{req_state}' but current state is '{actual}'"
                    )

    def apply_action(self, action: str) -> Optional[str]:
        """Apply *action* and advance FSM states. Returns affected subsystem name."""
        self.validate_action(action)
        for subsystem, rules in self._transitions.items():
            if action not in rules:
                continue
            rule = rules[action]
            old = self._states[subsystem]
            self._states[subsystem] = rule["target"]
            print(
                f"[{self.lab_id}] \U0001f504 子系统 '{subsystem}': {old} \u2192 {rule['target']}"
            )
            return subsystem
        return None

    def check_thresholds(self, telemetry: Dict[str, float]) -> List[Dict[str, Any]]:
        """Compare flat telemetry dict against configured thresholds.

        Returns list of violation dicts (empty if all OK).
        """
        violations: List[Dict[str, Any]] = []
        for key, value in telemetry.items():
            spec = self._thresholds.get(key)
            if spec is None:
                continue
            lo = spec.get("min")
            hi = spec.get("max")
            severity = spec.get("severity", "WARNING")
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                violations.append(
                    {"key": key, "value": value, "spec": spec, "severity": severity}
                )
        return violations

    def current_states(self) -> Dict[str, str]:
        return dict(self._states)
