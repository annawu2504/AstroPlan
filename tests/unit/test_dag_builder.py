"""Unit tests for DAGBuilder — control-flow-aware DAG construction."""
import pytest

from src.control.dag_builder import DAGBuilder
from src.types import Edge, PlanNode


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _dag(revision: str = "rev_001") -> DAGBuilder:
    return DAGBuilder(revision_id=revision, mission_id="test")


# ------------------------------------------------------------------
# Sequence — linear chain
# ------------------------------------------------------------------

def test_sequence_builds_linear_chain():
    dag = _dag()
    dag.set_context("sequence")
    a = dag.register_action("activate_pump", {}, "pump")
    b = dag.register_action("heat_to_40",   {}, "thermal")
    c = dag.register_action("activate_camera", {}, "camera")

    resp = dag.to_plan_response()
    by_id = {n.node_id: n for n in resp.nodes}

    assert a in by_id
    assert b in by_id
    assert c in by_id
    assert a in by_id[b].depends_on
    assert b in by_id[c].depends_on
    assert not by_id[a].depends_on  # first node has no dependency


def test_sequence_node_count():
    dag = _dag()
    for i in range(4):
        dag.register_action(f"skill_{i}", {}, "sys")
    assert dag.node_count() == 4


# ------------------------------------------------------------------
# Parallel — fan-out from shared predecessor
# ------------------------------------------------------------------

def test_parallel_fanout_shares_predecessor():
    dag = _dag()
    dag.set_context("sequence")
    pre = dag.register_action("pre_step", {}, "all")

    dag.set_context("parallel", parallel_predecessor=pre)
    b = dag.register_action("branch_b", {}, "sys_b")
    c = dag.register_action("branch_c", {}, "sys_c")

    resp = dag.to_plan_response()
    by_id = {n.node_id: n for n in resp.nodes}

    # Both parallel nodes depend on the predecessor
    assert pre in by_id[b].depends_on
    assert pre in by_id[c].depends_on


def test_parallel_siblings_have_no_dependency_between_them():
    dag = _dag()
    dag.set_context("sequence")
    pre = dag.register_action("pre", {}, "all")

    dag.set_context("parallel", parallel_predecessor=pre)
    b = dag.register_action("b", {}, "sys")
    c = dag.register_action("c", {}, "sys")

    resp = dag.to_plan_response()
    by_id = {n.node_id: n for n in resp.nodes}

    assert b not in by_id[c].depends_on
    assert c not in by_id[b].depends_on


# ------------------------------------------------------------------
# Fallback — only first alternative registered
# ------------------------------------------------------------------

def test_fallback_registers_only_first_alternative():
    dag = _dag()
    dag.set_context("fallback")
    a = dag.register_action("primary",  {}, "sys")
    b = dag.register_action("backup_1", {}, "sys")  # dropped
    c = dag.register_action("backup_2", {}, "sys")  # dropped

    # register_action returns "" for dropped nodes
    assert a != ""
    assert b == ""
    assert c == ""

    resp = dag.to_plan_response()
    node_ids = {n.node_id for n in resp.nodes}
    assert a in node_ids
    assert dag.node_count() == 1


def test_fallback_resets_after_set_context():
    """A new set_context("fallback") call resets the first-registered flag."""
    dag = _dag()
    dag.set_context("fallback")
    a = dag.register_action("first", {}, "sys")
    dag.register_action("dropped", {}, "sys")  # silently dropped

    dag.set_context("fallback")  # reset — next call is the new "first"
    b = dag.register_action("second_first", {}, "sys")

    assert a != "" and b != ""
    assert dag.node_count() == 2


# ------------------------------------------------------------------
# Cycle detection
# ------------------------------------------------------------------

def test_cycle_detection_raises():
    dag = _dag()
    a = dag.register_action("skill_a", {}, "sys")
    b = dag.register_action("skill_b", {}, "sys")
    # Manually inject a back-edge: b → a creates a cycle (a → b → a)
    dag._edges.append(Edge(from_id=b, to_id=a))

    with pytest.raises(ValueError, match="cycle"):
        dag.to_plan_response()


def test_acyclic_dag_validates_without_error():
    dag = _dag()
    dag.register_action("a", {}, "sys")
    dag.register_action("b", {}, "sys")
    dag.register_action("c", {}, "sys")
    response = dag.to_plan_response()
    assert response.validate() is True


# ------------------------------------------------------------------
# seed_completed_node
# ------------------------------------------------------------------

def test_seed_completed_node_appears_in_response():
    dag = _dag("rev_002")
    completed = PlanNode(
        node_id="rev_001_n1",
        lineage_id="abc123",
        skill_name="activate_pump",
        params={},
        depends_on=[],
        required_roles=[],
        tool_hints=[],
        interruptible=True,
    )
    dag.seed_completed_node(completed)
    resp = dag.to_plan_response()
    node_ids = {n.node_id for n in resp.nodes}
    assert "rev_001_n1" in node_ids


# ------------------------------------------------------------------
# Metadata
# ------------------------------------------------------------------

def test_node_and_edge_counts():
    dag = _dag()
    assert dag.node_count() == 0
    assert dag.edge_count() == 0
    dag.register_action("a", {}, "s")
    dag.register_action("b", {}, "s")
    assert dag.node_count() == 2
    assert dag.edge_count() == 1  # a → b


def test_to_plan_response_uses_revision_id():
    dag = _dag("my_rev")
    dag.register_action("skill", {}, "s")
    resp = dag.to_plan_response()
    assert resp.revision_id == "my_rev"
    assert resp.nodes[0].node_id.startswith("my_rev")
