"""Unit tests for ManualParser — LLM-based experiment manual → Milestone extractor."""
import json
import pytest

from src.memory.manual_parser import ManualParser
from src.types import Milestone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockLLM:
    """Minimal LLM stub that returns a pre-configured string."""

    def __init__(self, response: str):
        self._response = response

    def call(self, prompt: str) -> str:
        return self._response


class _MockRegistry:
    """Minimal registry stub with a fixed set of skill names."""

    def __init__(self, skills: list):
        self._skills = skills

    def skill_names(self) -> list:
        return list(self._skills)


def _make_parser(response: str, skills=None) -> ManualParser:
    if skills is None:
        skills = ["activate_pump", "heat_to_40", "cool_down", "activate_camera"]
    return ManualParser(
        llm_client=_MockLLM(response),
        registry=_MockRegistry(skills),
    )


def _valid_record(**overrides) -> dict:
    base = {
        "goal": "Activate pump and heat sample",
        "pre_states": {"fluid_pump": "IDLE"},
        "steps": ["activate_pump", "heat_to_40"],
        "post_states": {"fluid_pump": "ACTIVE", "thermal": "HEATING"},
        "safety_thresholds": {"temperature": 45.0},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# No LLM
# ---------------------------------------------------------------------------

def test_parse_no_llm_returns_empty():
    parser = ManualParser(llm_client=None, registry=_MockRegistry(["activate_pump"]))
    result = parser.parse("some manual text", lab_id="test-lab")
    assert result == []


def test_parse_empty_registry_returns_empty():
    parser = ManualParser(
        llm_client=_MockLLM(json.dumps([_valid_record()])),
        registry=_MockRegistry([]),
    )
    result = parser.parse("manual text", lab_id="test-lab")
    assert result == []


# ---------------------------------------------------------------------------
# Valid LLM response
# ---------------------------------------------------------------------------

def test_parse_valid_response_returns_milestones():
    records = [_valid_record()]
    parser = _make_parser(json.dumps(records))
    milestones = parser.parse("manual text", lab_id="test-lab")
    assert len(milestones) == 1
    m = milestones[0]
    assert isinstance(m, Milestone)
    assert m.task_vector.goal_text == "Activate pump and heat sample"
    assert [s.skill_name for s in m.trajectory.steps] == ["activate_pump", "heat_to_40"]


def test_parse_multiple_records():
    records = [
        _valid_record(goal="Phase 1", steps=["activate_pump"]),
        _valid_record(goal="Phase 2", steps=["heat_to_40", "activate_camera"]),
    ]
    parser = _make_parser(json.dumps(records))
    milestones = parser.parse("manual", lab_id="lab")
    assert len(milestones) == 2


def test_parse_milestone_id_is_stable():
    """Same (goal, steps, lab_id) always produces the same milestone_id."""
    records = [_valid_record()]
    parser = _make_parser(json.dumps(records))
    m1 = parser.parse("text", lab_id="lab")[0]
    m2 = parser.parse("text", lab_id="lab")[0]
    assert m1.milestone_id == m2.milestone_id


def test_parse_milestone_task_vector_keywords_populated():
    records = [_valid_record()]
    parser = _make_parser(json.dumps(records))
    m = parser.parse("manual", lab_id="lab")[0]
    assert len(m.task_vector.keywords) > 0


def test_parse_constraints_pre_post_states():
    records = [_valid_record(
        pre_states={"fluid_pump": "IDLE"},
        post_states={"fluid_pump": "ACTIVE"},
    )]
    parser = _make_parser(json.dumps(records))
    m = parser.parse("manual", lab_id="lab")[0]
    assert m.constraints.required_preconditions == {"fluid_pump": "IDLE"}
    assert m.constraints.postconditions == {"fluid_pump": "ACTIVE"}


def test_parse_safety_thresholds_converted_to_float():
    records = [_valid_record(safety_thresholds={"temperature": "45"})]
    parser = _make_parser(json.dumps(records))
    m = parser.parse("manual", lab_id="lab")[0]
    assert m.constraints.safety_thresholds == {"temperature": 45.0}


# ---------------------------------------------------------------------------
# Unknown / invalid skills are filtered
# ---------------------------------------------------------------------------

def test_parse_unknown_skills_filtered():
    records = [_valid_record(steps=["activate_pump", "nonexistent_skill"])]
    parser = _make_parser(json.dumps(records))
    m = parser.parse("manual", lab_id="lab")[0]
    assert [s.skill_name for s in m.trajectory.steps] == ["activate_pump"]


def test_parse_all_unknown_skills_skips_milestone():
    records = [_valid_record(steps=["ghost_skill", "phantom_skill"])]
    parser = _make_parser(json.dumps(records))
    result = parser.parse("manual", lab_id="lab")
    assert result == []


def test_parse_mixed_valid_invalid_records():
    records = [
        _valid_record(goal="valid", steps=["activate_pump"]),
        _valid_record(goal="invalid", steps=["no_such_skill"]),
    ]
    parser = _make_parser(json.dumps(records))
    result = parser.parse("manual", lab_id="lab")
    assert len(result) == 1
    assert result[0].task_vector.goal_text == "valid"


# ---------------------------------------------------------------------------
# Malformed / unexpected LLM output
# ---------------------------------------------------------------------------

def test_parse_invalid_json_returns_empty():
    parser = _make_parser("this is not json at all")
    result = parser.parse("manual", lab_id="lab")
    assert result == []


def test_parse_json_object_not_array_returns_empty():
    parser = _make_parser(json.dumps({"goal": "oops", "steps": ["activate_pump"]}))
    result = parser.parse("manual", lab_id="lab")
    assert result == []


def test_parse_empty_array_returns_empty():
    parser = _make_parser("[]")
    result = parser.parse("manual", lab_id="lab")
    assert result == []


def test_parse_response_with_markdown_fence():
    records = [_valid_record()]
    fenced = f"```json\n{json.dumps(records)}\n```"
    parser = _make_parser(fenced)
    result = parser.parse("manual", lab_id="lab")
    assert len(result) == 1


def test_parse_response_with_preamble_text():
    records = [_valid_record()]
    response = f"Here are the milestones:\n{json.dumps(records)}\n"
    parser = _make_parser(response)
    result = parser.parse("manual", lab_id="lab")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Partial / missing fields — graceful defaults
# ---------------------------------------------------------------------------

def test_parse_missing_pre_states_defaults_to_empty():
    records = [{"goal": "heat", "steps": ["heat_to_40"]}]
    parser = _make_parser(json.dumps(records))
    m = parser.parse("manual", lab_id="lab")[0]
    assert m.constraints.required_preconditions == {}


def test_parse_missing_post_states_defaults_to_empty():
    records = [{"goal": "heat", "steps": ["heat_to_40"]}]
    parser = _make_parser(json.dumps(records))
    m = parser.parse("manual", lab_id="lab")[0]
    assert m.constraints.postconditions == {}


def test_parse_missing_safety_thresholds_defaults_to_empty():
    records = [{"goal": "heat", "steps": ["heat_to_40"]}]
    parser = _make_parser(json.dumps(records))
    m = parser.parse("manual", lab_id="lab")[0]
    assert m.constraints.safety_thresholds == {}


def test_parse_record_without_goal_is_skipped():
    records = [{"steps": ["activate_pump"]}]
    parser = _make_parser(json.dumps(records))
    result = parser.parse("manual", lab_id="lab")
    assert result == []


def test_parse_non_dict_record_is_skipped():
    response = json.dumps([["activate_pump"], _valid_record(goal="ok", steps=["heat_to_40"])])
    parser = _make_parser(response)
    result = parser.parse("manual", lab_id="lab")
    # The list element is skipped; only the dict element survives
    assert len(result) == 1


# ---------------------------------------------------------------------------
# LLM call failure
# ---------------------------------------------------------------------------

class _FailingLLM:
    def call(self, prompt: str) -> str:
        raise RuntimeError("network error")


def test_parse_llm_exception_returns_empty():
    parser = ManualParser(
        llm_client=_FailingLLM(),
        registry=_MockRegistry(["activate_pump"]),
    )
    result = parser.parse("manual", lab_id="lab")
    assert result == []
