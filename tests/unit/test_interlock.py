"""Unit tests for InterlockEngine FSM validation and threshold checking."""
import pytest
from src.physics.interlock_engine import InterlockEngine, InterlockViolation


def _make_engine() -> InterlockEngine:
    subsystems = {
        "pump": {
            "initial": "idle",
            "transitions": {
                "idle":    {"activate_pump":   "running"},
                "running": {"deactivate_pump": "idle"},
            },
        },
        "thermal": {
            "initial": "cold",
            "transitions": {
                "cold": {"heat_to_40": "hot"},
                "hot":  {"cool_down":  "cold"},
            },
        },
    }
    thresholds = {
        "temperature": {"max": 50.0, "severity": "WARNING"},
        "pressure":    {"min": 1.0,  "max": 10.0, "severity": "CRITICAL"},
    }
    return InterlockEngine(subsystems=subsystems, thresholds=thresholds, lab_id="test-lab")


# ------------------------------------------------------------------
# validate_action
# ------------------------------------------------------------------

def test_validate_action_passes_when_state_matches():
    engine = _make_engine()
    engine.validate_action("activate_pump")  # pump is idle — OK


def test_validate_action_raises_when_wrong_state():
    engine = _make_engine()
    with pytest.raises(InterlockViolation):
        engine.validate_action("deactivate_pump")  # pump is idle, not running


def test_validate_action_raises_for_unknown_skill():
    engine = _make_engine()
    with pytest.raises(InterlockViolation, match="not registered"):
        engine.validate_action("nonexistent_skill")


def test_validate_action_blocked_after_state_advance():
    engine = _make_engine()
    engine.apply_action("activate_pump")  # pump → running
    with pytest.raises(InterlockViolation):
        engine.validate_action("activate_pump")  # already running, can't activate again


# ------------------------------------------------------------------
# apply_action
# ------------------------------------------------------------------

def test_apply_action_advances_fsm():
    engine = _make_engine()
    engine.apply_action("activate_pump")
    assert engine.state("pump") == "running"


def test_apply_action_full_cycle():
    engine = _make_engine()
    engine.apply_action("activate_pump")
    engine.apply_action("deactivate_pump")
    assert engine.state("pump") == "idle"


def test_apply_action_returns_subsystem_name():
    engine = _make_engine()
    subsystem = engine.apply_action("heat_to_40")
    assert subsystem == "thermal"


def test_current_states_reflects_all_subsystems():
    engine = _make_engine()
    states = engine.current_states()
    assert states["pump"] == "idle"
    assert states["thermal"] == "cold"


# ------------------------------------------------------------------
# check_thresholds
# ------------------------------------------------------------------

def test_check_thresholds_no_violation():
    engine = _make_engine()
    violations = engine.check_thresholds({"temperature": 30.0})
    assert violations == []


def test_check_thresholds_max_violation():
    engine = _make_engine()
    violations = engine.check_thresholds({"temperature": 60.0})
    assert len(violations) == 1
    assert violations[0]["key"] == "temperature"
    assert violations[0]["value"] == 60.0


def test_check_thresholds_min_violation():
    engine = _make_engine()
    violations = engine.check_thresholds({"pressure": 0.5})
    assert len(violations) == 1
    assert violations[0]["key"] == "pressure"


def test_check_thresholds_multiple_violations():
    engine = _make_engine()
    violations = engine.check_thresholds({"temperature": 60.0, "pressure": 0.1})
    assert len(violations) == 2


def test_check_thresholds_ignores_unknown_keys():
    engine = _make_engine()
    violations = engine.check_thresholds({"humidity": 99.0})
    assert violations == []
