"""Unit tests for SkillLibrary — observation accumulation, promotion, persistence."""
import os
import tempfile

import pytest

from src.memory.skill_library import SkillLibrary
from src.types import AtomicSkillRecord, MilestoneStateDescription, PhysicalConstraints


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _steps(*names: str) -> list:
    return [AtomicSkillRecord(skill_name=n) for n in names]


def _state(**fsm_states) -> MilestoneStateDescription:
    return MilestoneStateDescription(subsystem_states=dict(fsm_states))


def _observe(lib: SkillLibrary, goal: str, skills: list, success: bool = True) -> None:
    lib.observe(
        steps=_steps(*skills),
        goal_text=goal,
        state_before=_state(),
        state_after=_state(),
        constraints=PhysicalConstraints(),
        success=success,
    )


# ------------------------------------------------------------------
# observe
# ------------------------------------------------------------------

def test_observe_accumulates_patterns():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    _observe(lib, "run pump", ["activate_pump", "heat_to_40"])
    assert lib.pattern_count() == 1


def test_observe_same_pattern_increments_count():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    _observe(lib, "run pump", ["activate_pump"])
    _observe(lib, "run pump", ["activate_pump"])
    assert lib.pattern_count() == 1   # same goal+steps → same pattern_id
    milestones = lib.export_milestones()
    assert milestones[0].trajectory.observation_count == 2


def test_observe_ignores_failed_runs():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    _observe(lib, "pump fail", ["activate_pump"], success=False)
    assert lib.pattern_count() == 0


def test_observe_ignores_empty_steps():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    lib.observe(
        steps=[],
        goal_text="empty",
        state_before=_state(),
        state_after=_state(),
        constraints=PhysicalConstraints(),
        success=True,
    )
    assert lib.pattern_count() == 0


# ------------------------------------------------------------------
# min_promote threshold
# ------------------------------------------------------------------

def test_min_promote_2_blocks_first_observation():
    lib = SkillLibrary(lab_id="test", min_promote=2)
    _observe(lib, "heat sample", ["heat_to_40"])
    assert lib.promoted_count() == 0
    assert lib.export_milestones() == []


def test_min_promote_2_promotes_on_second_observation():
    lib = SkillLibrary(lab_id="test", min_promote=2)
    _observe(lib, "heat sample", ["heat_to_40"])
    _observe(lib, "heat sample", ["heat_to_40"])
    assert lib.promoted_count() == 1
    assert len(lib.export_milestones()) == 1


def test_min_promote_1_promotes_immediately():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    _observe(lib, "instant", ["activate_camera"])
    assert lib.promoted_count() == 1


# ------------------------------------------------------------------
# export_milestones
# ------------------------------------------------------------------

def test_export_milestone_fields():
    lib = SkillLibrary(lab_id="test-lab", min_promote=1)
    lib.observe(
        steps=_steps("activate_pump", "heat_to_40"),
        goal_text="heat the fluid sample",
        state_before=_state(pump="idle"),
        state_after=_state(pump="running", thermal="hot"),
        constraints=PhysicalConstraints(
            required_preconditions={"pump": "idle"},
            postconditions={"thermal": "hot"},
        ),
        success=True,
    )
    milestones = lib.export_milestones()
    assert len(milestones) == 1
    m = milestones[0]

    assert m.task_vector.goal_text == "heat the fluid sample"
    assert m.task_vector.mission_id == "test-lab"
    assert len(m.trajectory.steps) == 2
    assert m.trajectory.steps[0].skill_name == "activate_pump"
    assert m.trajectory.success_rate == 1.0
    assert m.constraints.required_preconditions == {"pump": "idle"}


def test_export_success_rate_updates():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    # Two observations, both successful → success_rate = 1.0
    _observe(lib, "pump run", ["activate_pump"])
    _observe(lib, "pump run", ["activate_pump"])
    m = lib.export_milestones()[0]
    assert m.trajectory.success_rate == 1.0


# ------------------------------------------------------------------
# save / load
# ------------------------------------------------------------------

def test_save_load_roundtrip():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    lib.observe(
        steps=_steps("activate_pump", "heat_to_40"),
        goal_text="roundtrip test",
        state_before=_state(pump="idle"),
        state_after=_state(thermal="hot"),
        constraints=PhysicalConstraints(required_preconditions={"pump": "idle"}),
        success=True,
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as fh:
        path = fh.name
    try:
        lib.save(path)

        lib2 = SkillLibrary(lab_id="test", min_promote=1)
        lib2.load(path)

        assert lib2.pattern_count() == 1
        milestones = lib2.export_milestones()
        assert len(milestones) == 1
        m = milestones[0]
        assert m.task_vector.goal_text == "roundtrip test"
        assert len(m.trajectory.steps) == 2
    finally:
        os.unlink(path)


def test_load_nonexistent_file_is_noop():
    lib = SkillLibrary(lab_id="test", min_promote=1)
    lib.load("/nonexistent/path/that/does/not/exist.json")
    assert lib.pattern_count() == 0


def test_load_merges_with_existing_patterns():
    """Loading should merge counts, not overwrite existing data."""
    lib = SkillLibrary(lab_id="test", min_promote=1)
    _observe(lib, "merge test", ["activate_pump"])

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as fh:
        path = fh.name
    try:
        lib.save(path)

        lib2 = SkillLibrary(lab_id="test", min_promote=1)
        _observe(lib2, "merge test", ["activate_pump"])  # 1 observation in memory
        lib2.load(path)  # file also has 1 → max(1, 1) = 1

        assert lib2.pattern_count() == 1
    finally:
        os.unlink(path)
