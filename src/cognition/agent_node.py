"""AgentNode — Layer 4 reasoning node.

Drives an LLM (or mock planner) to produce a step-level AgentDecision
from a sub-goal, SharedContext, and retrieved Milestones.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from src.types import AgentDecision, Milestone, NodeRunContext, SharedContext, TreeExecutionResult

# Type alias for skill catalog: {name: description}
SkillMap = Dict[str, str]


class AgentNode:
    """One node in the hierarchical LLM agent tree.

    Parameters
    ----------
    node_id:
        Unique identifier within the tree (e.g. ``"root"``, ``"thermal_branch"``).
    llm_client:
        Any object with a ``call(prompt: str) -> str`` method.  Pass ``None``
        to fall back to the built-in mock planner.
    depth:
        Current depth in the tree (root = 0)
    available_skills:
        Dict mapping skill name → description.  Injected into the LLM prompt so
        the model can produce grounded Act actions rather than guessing names.
        Accepts either a Dict[str, str] or a List[str] (descriptions default to "").
    """

    def __init__(
        self,
        node_id: str,
        llm_client: Optional[Any] = None,
        depth: int = 0,
        available_skills: Optional[Any] = None,
        milestone_engine: Optional[Any] = None,
    ):
        self.node_id = node_id
        self._llm = llm_client
        self.depth = depth
        self.goal: str = ""  # Set by parent when creating child nodes
        self._milestone_engine = milestone_engine  # MilestoneEngine or None
        # Normalise to Dict[str, str] regardless of input type
        if available_skills is None:
            self.available_skills: SkillMap = {}
        elif isinstance(available_skills, dict):
            self.available_skills = available_skills
        else:
            self.available_skills = {s: "" for s in available_skills}

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def execute_decision(
        self,
        sub_goal: str,
        context: SharedContext,
        milestones: List[Milestone],
    ) -> AgentDecision:
        """Produce an AgentDecision for the given sub-goal.

        Uses the LLM when available; falls back to a rule-based mock
        that covers the skill set for each lab demo.
        """
        if self._llm is not None:
            return self._llm_plan(sub_goal, context, milestones)
        return self._mock_plan(sub_goal, context)

    # ------------------------------------------------------------------
    # LLM planning path
    # ------------------------------------------------------------------

    def _llm_plan(
        self, sub_goal: str, context: SharedContext, milestones: List[Milestone]
    ) -> AgentDecision:
        prompt = self._build_prompt(sub_goal, context, milestones)
        raw = self._llm.call(prompt)
        decision = self._parse_llm_response(raw)

        # Retry once with a more directive prompt when model returns Think
        # or an Expand with empty subgoals — both produce 0 DAG nodes.
        _empty_expand = (
            decision.skill == "Expand"
            and not decision.action.get("subgoals")
        )
        if (decision.skill == "Think" or _empty_expand) and self.available_skills:
            print(
                f"[AgentNode:{self.node_id}] LLM returned {decision.skill} "
                f"(empty_expand={_empty_expand}) for '{sub_goal[:60]}'. "
                "Retrying with focused prompt."
            )
            retry_raw = self._llm.call(self._build_focused_prompt(sub_goal))
            retry_decision = self._parse_llm_response(retry_raw)
            if retry_decision.skill in ("Act", "Expand") and (
                retry_decision.skill == "Act"
                or retry_decision.action.get("subgoals")
            ):
                return retry_decision
            print(
                f"[AgentNode:{self.node_id}] Retry also returned "
                f"{retry_decision.skill}. Caller will apply mock fallback."
            )
            return retry_decision  # caller (AstroPlan.plan) handles 0-node fallback

        return decision

    def _build_prompt(
        self,
        sub_goal: str,
        context: SharedContext,
        milestones: List[Milestone],
    ) -> str:
        # Render top-2 milestones using 4-tuple fields; cap steps at 8 to stay compact.
        milestone_txt = ""
        for m in milestones[:2]:
            step_names = [s.skill_name for s in m.trajectory.steps[:8]]
            pre_states = m.state_description.subsystem_states
            milestone_txt += (
                f"\n- Goal: {m.task_vector.goal_text}"
                f"\n  Pre-state: {pre_states}"
                f"\n  Steps ({len(m.trajectory.steps)}): {step_names}"
                f"\n  Success rate: {m.trajectory.success_rate:.0%}"
            )

        path_parts = self.node_id.split("_")
        tree_path = " > ".join(path_parts) if len(path_parts) > 1 else self.node_id
        # Only include non-empty skill names from the action log
        completed_skills = [
            e.get("skill", "") for e in context.action_log if e.get("skill")
        ]
        completed_txt = ", ".join(completed_skills) if completed_skills else "none"

        if self.available_skills:
            lines = []
            for sname, sdesc in self.available_skills.items():
                if sdesc:
                    # Truncate long descriptions to keep prompt compact
                    words = sdesc.split()
                    one_liner = " ".join(words[:20])
                    lines.append(f"  - {sname}: {one_liner}")
                else:
                    lines.append(f"  - {sname}")
            skills_section = (
                "Available MCP skills (use exact names):\n"
                + "\n".join(lines)
                + "\n\n"
            )
        else:
            skills_section = ""

        # Use concrete JSON examples (no inline comments, no pipe syntax)
        # so that small models can follow the format reliably.
        act_example = (
            '{"skill":"Act",'
            '"action":{"skill":"EXACT_SKILL_NAME","params":{}},'
            '"reasoning":"one sentence"}'
        )
        expand_example = (
            '{"skill":"Expand",'
            '"action":{"control_flow":"Sequence","subgoals":["skill_a","skill_b"]},'
            '"reasoning":"one sentence"}'
        )

        return (
            "You are an autonomous space-lab planning agent.\n"
            f"Lab: {context.lab_id}  |  Node: {tree_path} (depth={self.depth})\n"
            f"Sub-goal: {sub_goal}\n"
            f"Already completed: {completed_txt}\n"
            f"Subsystem states: {json.dumps(context.subsystem_states, ensure_ascii=False)}\n\n"
            f"{skills_section}"
            "DECISION RULES (apply in order):\n"
            "  1. Sub-goal is exactly one skill name listed above → output Act.\n"
            "  2. Sub-goal requires multiple ordered skills → output Expand with all "
            "needed skill names as subgoals.\n"
            "  3. NEVER output Think when skills are available; always produce an action.\n\n"
            "Output ONLY a single valid JSON object. No extra text, no markdown fences.\n\n"
            f"Act:    {act_example}\n\n"
            f"Expand: {expand_example}\n"
        )

    def _build_focused_prompt(self, sub_goal: str) -> str:
        """Minimal directive prompt used on retry when the model fails to act."""
        skill_names = list(self.available_skills.keys())
        names_str = ", ".join(f'"{s}"' for s in skill_names)
        act_ex = '{"skill":"Act","action":{"skill":"SKILL_NAME","params":{}},"reasoning":""}'
        exp_ex = (
            '{"skill":"Expand","action":{'
            '"control_flow":"Sequence","subgoals":["skill1","skill2"]},'
            '"reasoning":""}'
        )
        return (
            f"Space-lab task: {sub_goal}\n\n"
            f"Available skills: {names_str}\n\n"
            "Choose ONE action:\n"
            f"  Single skill → {act_ex}\n"
            f"  Multiple skills → {exp_ex}\n\n"
            "Output ONLY the JSON object:"
        )

    def _parse_llm_response(self, raw: str) -> AgentDecision:
        """Parse LLM output into an AgentDecision.

        Handles markdown code fences, extra surrounding text, nested braces,
        and lowercase skill-type names from smaller models.
        """
        import re as _re
        text = raw.strip()

        # 1. Strip markdown code fences if present
        fence = _re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if fence:
            text = fence.group(1).strip()

        # 2. Find the outermost JSON object using brace-counting
        start = text.find("{")
        if start == -1:
            print(
                f"[AgentNode:{self.node_id}] No JSON object in LLM response. "
                f"raw[:200]={raw[:200]!r}"
            )
            return AgentDecision(skill="Think", reasoning=raw[:500])

        depth_count = 0
        end = start
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth_count += 1
            elif ch == "}":
                depth_count -= 1
                if depth_count == 0:
                    end = i + 1
                    break

        try:
            data = json.loads(text[start:end])
        except json.JSONDecodeError as exc:
            print(
                f"[AgentNode:{self.node_id}] JSON decode error: {exc}. "
                f"Extracted: {text[start:end][:200]!r}"
            )
            return AgentDecision(skill="Think", reasoning=raw[:500])

        # 3. Normalize skill type — small models sometimes return lowercase
        raw_skill_type = data.get("skill", "Act")
        _normalize = {"think": "Think", "act": "Act", "expand": "Expand"}
        decision_skill = _normalize.get(
            str(raw_skill_type).lower(), raw_skill_type
        )

        action = data.get("action", {})
        reasoning = data.get("reasoning", "")

        # 4. Guard: Expand with empty subgoals is equivalent to Think
        if decision_skill == "Expand" and not action.get("subgoals"):
            print(
                f"[AgentNode:{self.node_id}] Expand had empty subgoals — "
                f"treating as Think. full={data}"
            )
            return AgentDecision(skill="Think", reasoning=reasoning or raw[:300])

        return AgentDecision(skill=decision_skill, action=action, reasoning=reasoning)

    # ------------------------------------------------------------------
    # Mock planning path (no LLM required)
    # ------------------------------------------------------------------

    # Default skill sequence used when no catalog is loaded
    _DEFAULT_SKILL_SEQUENCE = [
        {"skill": "activate_pump", "params": {}},
        {"skill": "heat_to_40", "params": {}},
        {"skill": "activate_camera", "params": {}},
    ]

    # Skills that should only run when the goal explicitly requests them.
    # Including these in a generic "full experiment" plan corrupts final FSM state.
    _EMERGENCY_SKILL_NAMES = frozenset({"emergency_stop", "emergency_stop_print"})
    # Keywords that indicate an emergency/abort mission (Chinese and English).
    # "stop" is intentionally excluded — too broad, matches normal steps like "stop the pump".
    # Note: applies only in the mock-planner path (no LLM configured).
    _EMERGENCY_GOAL_KEYWORDS = frozenset({
        # English
        "emergency", "abort",
        # Chinese
        "紧急", "中止", "异常", "故障",
    })

    def _mock_plan(
        self, sub_goal: str, context: SharedContext
    ) -> AgentDecision:
        """Rule-based fallback planner (multi-lab aware).

        Decision logic (in priority order):
        1. If sub_goal is an exact registered skill name → Act immediately.
        2. If sub_goal contains " and " → Expand by splitting on " and ".
        3. Otherwise treat as a high-level goal → Expand into remaining skills.
           Emergency/abort skills are excluded from the expansion unless the
           goal itself mentions emergency keywords, preventing them from
           corrupting the FSM state in normal-experiment benchmarks.
        """
        # Use skills from catalog if available, else fall back to defaults
        if self.available_skills:
            sequence = [{"skill": s, "params": {}} for s in self.available_skills]
        else:
            sequence = self._DEFAULT_SKILL_SEQUENCE
        known = {step["skill"]: step for step in sequence}

        # Case 1: goal IS an atomic skill name — execute it directly
        if sub_goal in known:
            return AgentDecision(
                skill="Act",
                action=known[sub_goal],
                reasoning=f"Mock planner: executing atomic skill '{sub_goal}'",
            )

        # Case 2: English compound goal split on " and "
        if " and " in sub_goal.lower():
            subgoals = [s.strip() for s in sub_goal.split(" and ")]
            return AgentDecision(
                skill="Expand",
                action={"control_flow": "Sequence", "subgoals": subgoals},
                reasoning=f"Mock planner: decomposing compound goal into {len(subgoals)} sub-goals",
            )

        # Case 3: high-level goal — expand into remaining skills, filtered by goal type
        done = {e["skill"] for e in context.action_log}
        goal_lower = sub_goal.lower()
        is_emergency_goal = any(kw in goal_lower for kw in self._EMERGENCY_GOAL_KEYWORDS)

        if is_emergency_goal:
            # Emergency goal: prefer emergency/abort skills; fall back to all
            emergency_available = [
                s for s in known
                if s not in done and s in self._EMERGENCY_SKILL_NAMES
            ]
            remaining = emergency_available or [s for s in known if s not in done]
        else:
            # Normal goal: exclude pure emergency skills to avoid corrupting FSM state
            remaining = [
                s for s in known
                if s not in done and s not in self._EMERGENCY_SKILL_NAMES
            ]
            if not remaining:
                remaining = [s for s in known if s not in done]

        if remaining:
            return AgentDecision(
                skill="Expand",
                action={"control_flow": "Sequence", "subgoals": remaining},
                reasoning=(
                    f"Mock planner: expanding {'emergency' if is_emergency_goal else 'high-level'} "
                    f"goal into {len(remaining)} skill sub-goals"
                ),
            )

        return AgentDecision(
            skill="Think",
            action={},
            reasoning="All mock skills completed.",
        )

    # ------------------------------------------------------------------
    # Recursive execution
    # ------------------------------------------------------------------

    async def run(
        self,
        rctx: NodeRunContext,
        step_id: int,
        decision_id: int,
    ) -> TreeExecutionResult:
        """Execute this agent node recursively.

        Parameters
        ----------
        rctx:
            Per-call invariants: world-state context, log, max_depth, env.
        step_id:
            Current step counter (incremented for each Act).
        decision_id:
            Current decision counter (incremented for each Think/Act/Expand).

        Returns
        -------
        TreeExecutionResult with success status and updated counters.
        """
        if self.depth > rctx.max_depth:
            return TreeExecutionResult(
                success=False,
                step_id=step_id,
                decision_id=decision_id,
                terminate_reason="max_depth",
            )

        # Refresh context from live memory so _mock_plan Case 3 sees the
        # up-to-date action_log (completed skills logged by earlier siblings).
        live_context = rctx.context
        if hasattr(rctx.env, '_memory'):
            live_context = rctx.env._memory.snapshot()

        # Retrieve relevant milestones from engine (empty list if no engine loaded)
        sub_goal = self.goal if self.goal else "complete mission"
        milestones = []
        if self._milestone_engine is not None:
            try:
                milestones = self._milestone_engine.retrieve(
                    query_state=live_context.telemetry,
                    goal=sub_goal,
                    top_k=2,
                    current_subsystem_states=live_context.subsystem_states,
                )
            except Exception:
                milestones = []

        # Get decision from planner
        decision = self.execute_decision(
            sub_goal=sub_goal,
            context=live_context,
            milestones=milestones,
        )
        decision_id += 1

        if decision.skill == "Think":
            # Just reasoning, no action
            rctx.log.append({
                "type": "think",
                "node_id": self.node_id,
                "reasoning": decision.reasoning,
                "decision_id": decision_id,
            })
            return TreeExecutionResult(success=True, step_id=step_id, decision_id=decision_id)

        elif decision.skill == "Act":
            # Execute atomic action
            success = await self._execute_action(decision.action, rctx)
            step_id += 1
            return TreeExecutionResult(success=success, step_id=step_id, decision_id=decision_id)

        elif decision.skill == "Expand":
            # Create sub-tree and delegate execution
            # Normalise control_flow case — small models may return "sequence"
            raw_cf = decision.action.get("control_flow", "Sequence") or "Sequence"
            _cf_map = {
                "sequence": "Sequence",
                "fallback": "Fallback",
                "parallel": "Parallel",
            }
            control_flow = _cf_map.get(raw_cf.lower(), raw_cf)
            subgoals = decision.action.get("subgoals", [])

            rctx.log.append({
                "type": "expand",
                "node_id": self.node_id,
                "control_flow": control_flow,
                "subgoals": subgoals,
                "reasoning": decision.reasoning,
                "decision_id": decision_id,
            })

            # Import here to avoid circular dependency
            from src.cognition.control_flow import ControlFlowNode

            cf_node = ControlFlowNode(control_type=control_flow, depth=self.depth + 1)
            for idx, subgoal in enumerate(subgoals):
                child_agent = AgentNode(
                    node_id=f"{self.node_id}_sub{idx}",
                    llm_client=self._llm,
                    depth=self.depth + 2,
                    available_skills=self.available_skills,
                    milestone_engine=self._milestone_engine,
                )
                child_agent.goal = subgoal
                cf_node.children.append(child_agent)

            # Delegate to sub-tree
            return await cf_node.run(rctx, step_id, decision_id)

        # Unknown skill type
        return TreeExecutionResult(success=False, step_id=step_id, decision_id=decision_id)

    async def _execute_action(
        self, action: Dict[str, Any], rctx: NodeRunContext
    ) -> bool:
        """Execute a single atomic action via the environment's execution pipeline.

        Parameters
        ----------
        action:
            Action dict with 'skill' and 'params' keys
        rctx:
            Per-call run context (env, log, etc.).

        Returns
        -------
        True if action succeeded, False otherwise
        """
        env = rctx.env
        log = rctx.log
        skill = action.get("skill", "noop")
        params = action.get("params", {})

        # ------------------------------------------------------------------
        # plan_mode=True: dry-run — register in DAG only, no dispatch
        # ------------------------------------------------------------------
        if getattr(env, 'plan_mode', False):
            import hashlib as _hl
            subsystem = env._skill_to_subsystem(skill)
            # Include positional counter so two calls to the same skill in one
            # mission get distinct lineage_ids (fixing same-skill collision).
            _occurrence = env._dag.node_count() + 1
            lineage_id = _hl.sha256(
                f"{env.lab_id}::{skill}::{_occurrence}".encode()
            ).hexdigest()[:12]
            env._dag.register_action(
                skill=skill,
                params=params,
                subsystem=subsystem,
                status="pending",
                lineage_id=lineage_id,
            )
            env._memory.log_action({"skill": skill, "params": params, "subsystem": subsystem})
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "planned",
            })
            return True

        # ------------------------------------------------------------------
        # plan_mode=False: normal execution path
        # ------------------------------------------------------------------

        # Interlock check
        try:
            env._interlock.validate_action(skill)
        except Exception as exc:
            print(f"[{env.lab_id}] Interlock blocked '{skill}': {exc}")
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "failed",
                "error": str(exc),
            })
            return False

        # Dispatch via MCP or direct HW
        action_payload = {
            "skill": skill,
            "params": params,
            "subsystem": env._skill_to_subsystem(skill),
        }

        try:
            if env._mcp.has_skill(skill):
                result = env._mcp.call(skill, params)
                if asyncio.iscoroutine(result):
                    result = await result
            else:
                # Serialize via OutputController — the single authorized
                # serialization point for hardware-bound payloads.
                wire = env._output.serialize_action(action_payload)
                tx = await env._hw.execute_instruction(wire)
                # For async actions, poll until done
                if skill in env._hw.ASYNC_ACTIONS:
                    for _ in range(30):
                        await asyncio.sleep(0.1)
                        res = await env._hw.poll_transaction(tx)
                        if res.status == "completed":
                            break

            # Derive RTT from telemetry packet age (space comms latency),
            # not from local MCP call timing.
            env._latency.record_from_telemetry(env._memory.snapshot().telemetry)

            # Log success
            env._memory.log_action(action_payload)
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "completed",
            })
            # Register completed atomic action in the DAG
            env._dag.register_action(
                skill=skill,
                params=params,
                subsystem=env._skill_to_subsystem(skill),
                status="completed",
            )
            return True

        except Exception as exc:
            # Sample RTT even on failure so the observer sees the attempt.
            env._latency.record_from_telemetry(env._memory.snapshot().telemetry)
            print(f"[{env.lab_id}] Action '{skill}' raised: {exc}")
            log.append({
                "type": "action",
                "node_id": self.node_id,
                "skill": skill,
                "params": params,
                "status": "failed",
                "error": str(exc),
            })
            # Register failed atomic action in the DAG so the full picture is preserved
            env._dag.register_action(
                skill=skill,
                params=params,
                subsystem=env._skill_to_subsystem(skill),
                status="failed",
            )
            return False
