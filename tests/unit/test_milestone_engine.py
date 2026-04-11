"""Unit tests for MilestoneEngine — BM25 indexing, FSM filtering, retrieval."""
import pytest

from src.memory.milestone_engine import MilestoneEngine
from src.types import (
    AtomicSkillRecord,
    Milestone,
    MilestoneStateDescription,
    PhysicalConstraints,
    TaskVector,
    TrajectoryFragment,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _milestone(
    goal: str,
    skills: list,
    preconditions: dict = None,
    success_rate: float = 1.0,
    keywords: list = None,
) -> Milestone:
    kw = keywords if keywords is not None else goal.lower().split()
    return Milestone(
        milestone_id=goal[:12].replace(" ", "_"),
        task_vector=TaskVector(
            mission_id="test",
            goal_text=goal,
            keywords=kw,
        ),
        state_description=MilestoneStateDescription(),
        trajectory=TrajectoryFragment(
            steps=[AtomicSkillRecord(skill_name=s) for s in skills],
            success_rate=success_rate,
        ),
        constraints=PhysicalConstraints(
            required_preconditions=preconditions or {}
        ),
    )


# ------------------------------------------------------------------
# build_index
# ------------------------------------------------------------------

def test_build_index_empty_gives_empty_retrieval():
    engine = MilestoneEngine()
    engine.build_index([])
    assert engine.retrieve(query_state={}, goal="any") == []


def test_build_index_replaces_previous():
    engine = MilestoneEngine()
    engine.build_index([_milestone("heat sample", ["heat_to_40"])])
    engine.build_index([])  # replace with empty
    assert engine.retrieve(query_state={}, goal="heat") == []


# ------------------------------------------------------------------
# filter_applicable
# ------------------------------------------------------------------

def test_filter_applicable_passes_matching_state():
    engine = MilestoneEngine()
    m = _milestone("pump running", ["activate_pump"], preconditions={"pump": "idle"})
    engine.build_index([m])
    applicable = engine.filter_applicable({"pump": "idle"})
    assert len(applicable) == 1


def test_filter_applicable_rejects_mismatched_state():
    engine = MilestoneEngine()
    m = _milestone("pump running", ["activate_pump"], preconditions={"pump": "idle"})
    engine.build_index([m])
    applicable = engine.filter_applicable({"pump": "running"})
    assert len(applicable) == 0


def test_filter_applicable_passes_no_preconditions():
    engine = MilestoneEngine()
    m = _milestone("free action", ["activate_camera"], preconditions={})
    engine.build_index([m])
    applicable = engine.filter_applicable({"pump": "idle"})
    assert len(applicable) == 1


def test_filter_applicable_partial_precondition_match():
    """All required preconditions must match — partial match is not sufficient."""
    engine = MilestoneEngine()
    m = _milestone(
        "combined",
        ["a"],
        preconditions={"pump": "running", "thermal": "hot"},
    )
    engine.build_index([m])
    # Only pump matches, thermal doesn't
    assert engine.filter_applicable({"pump": "running", "thermal": "cold"}) == []
    # Both match
    assert len(engine.filter_applicable({"pump": "running", "thermal": "hot"})) == 1


# ------------------------------------------------------------------
# retrieve
# ------------------------------------------------------------------

def test_retrieve_returns_at_most_top_k():
    engine = MilestoneEngine()
    milestones = [
        _milestone(f"goal {i}", [f"skill_{i}"], keywords=[f"goal", f"skill{i}"])
        for i in range(5)
    ]
    engine.build_index(milestones)
    results = engine.retrieve(query_state={}, goal="goal skill0", top_k=2)
    assert len(results) <= 2


def test_retrieve_scores_are_descending():
    engine = MilestoneEngine()
    milestones = [
        _milestone("heat fluid sample", ["activate_pump", "heat_to_40"], keywords=["heat", "fluid", "sample"]),
        _milestone("cool down camera",  ["cool_down"],                   keywords=["cool", "camera"]),
    ]
    engine.build_index(milestones)
    results = engine.retrieve(query_state={}, goal="heat fluid", top_k=2)
    if len(results) >= 2:
        assert results[0].score >= results[1].score


def test_retrieve_fsm_filter_applied():
    """Milestones whose preconditions fail must not appear in results."""
    engine = MilestoneEngine()
    milestones = [
        _milestone("heat", ["heat_to_40"], preconditions={"pump": "running"}, keywords=["heat"]),
        _milestone("cool", ["cool_down"],  preconditions={},                  keywords=["cool", "heat"]),
    ]
    engine.build_index(milestones)
    # pump is idle → "heat" milestone filtered out
    results = engine.retrieve(
        query_state={},
        goal="heat cool",
        top_k=5,
        current_subsystem_states={"pump": "idle"},
    )
    returned_steps = {s.skill_name for r in results for s in r.trajectory.steps}
    assert "heat_to_40" not in returned_steps


def test_retrieve_no_match_returns_empty():
    engine = MilestoneEngine()
    engine.build_index([_milestone("xyz special unique", ["skill_xyz"], keywords=["xyz"])])
    results = engine.retrieve(query_state={}, goal="completely different topic")
    assert results == []


def test_retrieve_score_populated_on_returned_milestones():
    engine = MilestoneEngine()
    engine.build_index([_milestone("heat sample", ["heat_to_40"], keywords=["heat", "sample"])])
    results = engine.retrieve(query_state={}, goal="heat sample")
    if results:
        assert results[0].score > 0.0


def test_retrieve_higher_success_rate_preferred():
    """All else equal, milestone with higher success_rate scores higher."""
    engine = MilestoneEngine()
    m_high = _milestone("run pump", ["activate_pump"], success_rate=0.95, keywords=["run", "pump"])
    m_low  = _milestone("run pump", ["activate_pump"], success_rate=0.10, keywords=["run", "pump"])
    # Give them distinct IDs so they coexist in the index
    m_low = Milestone(
        milestone_id="low_sr",
        task_vector=m_low.task_vector,
        state_description=m_low.state_description,
        trajectory=m_low.trajectory,
        constraints=m_low.constraints,
    )
    engine.build_index([m_high, m_low])
    results = engine.retrieve(query_state={}, goal="run pump", top_k=2)
    if len(results) == 2:
        assert results[0].trajectory.success_rate >= results[1].trajectory.success_rate


# ------------------------------------------------------------------
# compute_step_distance
# ------------------------------------------------------------------

def test_compute_step_distance_untouched():
    """No steps completed yet → distance is 1.0 (furthest away)."""
    engine = MilestoneEngine()
    m = _milestone("heat sample", ["activate_pump", "heat_to_40", "activate_camera"])
    assert engine.compute_step_distance([], m) == pytest.approx(1.0)


def test_compute_step_distance_complete():
    """All trajectory steps already done → distance is 0.0."""
    engine = MilestoneEngine()
    m = _milestone("heat sample", ["activate_pump", "heat_to_40"])
    assert engine.compute_step_distance(["activate_pump", "heat_to_40"], m) == pytest.approx(0.0)


def test_compute_step_distance_partial():
    """One of two steps done → distance is 0.5."""
    engine = MilestoneEngine()
    m = _milestone("heat sample", ["activate_pump", "heat_to_40"])
    assert engine.compute_step_distance(["activate_pump"], m) == pytest.approx(0.5)


def test_compute_step_distance_empty_trajectory():
    """Milestone with no trajectory steps → 0.0 (no division-by-zero)."""
    engine = MilestoneEngine()
    m = _milestone("empty", [])
    assert engine.compute_step_distance(["anything"], m) == pytest.approx(0.0)


def test_compute_step_distance_extra_completed_steps_ignored():
    """Extra completed steps beyond the trajectory do not push distance below 0."""
    engine = MilestoneEngine()
    m = _milestone("heat", ["heat_to_40"])
    # heat_to_40 done + extra unrelated skills
    dist = engine.compute_step_distance(["heat_to_40", "cool_down", "activate_camera"], m)
    assert dist == pytest.approx(0.0)


def test_compute_step_distance_decreases_as_steps_complete():
    """Distance strictly decreases as more trajectory steps are executed."""
    engine = MilestoneEngine()
    skills = ["activate_pump", "heat_to_40", "activate_camera"]
    m = _milestone("full experiment", skills)

    d0 = engine.compute_step_distance([], m)
    d1 = engine.compute_step_distance(["activate_pump"], m)
    d2 = engine.compute_step_distance(["activate_pump", "heat_to_40"], m)
    d3 = engine.compute_step_distance(["activate_pump", "heat_to_40", "activate_camera"], m)

    assert d0 > d1 > d2 > d3
    assert d3 == pytest.approx(0.0)


def test_compute_step_distance_order_independent():
    """Distance is based on set membership, not order of completion."""
    engine = MilestoneEngine()
    m = _milestone("heat", ["activate_pump", "heat_to_40"])
    # Same skills, different order
    assert engine.compute_step_distance(["heat_to_40", "activate_pump"], m) == \
           engine.compute_step_distance(["activate_pump", "heat_to_40"], m)
