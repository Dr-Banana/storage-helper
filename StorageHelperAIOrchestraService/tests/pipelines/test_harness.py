"""
Harness structural tests.

Three groups:
  1. ToolDeclaration integrity  — structural constraints declared in TOOL_DECLARATIONS
     are well-formed and internally consistent.
  2. RoutingGuide evaluation    — guide reads force_when rules from declarations and
     produces correct GuideResult / None.
  3. ResponseSensor validation  — sensor detects and auto-corrects LLM anomalies.

All tests are deterministic and offline (no LLM calls, no I/O).
"""
from __future__ import annotations

import pytest
from typing import Any, Dict, List

from app.skills.tool_registry import TOOL_DECLARATIONS
from app.pipelines.harness import (
    AnomalyKind,
    CLARIFICATION_NEEDED,
    CLARIFICATION_HINTS,
    RoutingGuide,
    ResponseSensor,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def guide():
    return RoutingGuide()


@pytest.fixture
def sensor():
    return ResponseSensor()


@pytest.fixture
def empty_state() -> Dict[str, Any]:
    return {}


@pytest.fixture
def pending_confirm_state() -> Dict[str, Any]:
    return {
        "pending_planning_queue": [{"date": "2026-05-05", "meal_time": "dinner"}],
        "last_pipeline_action": "ask_confirm_dates",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. ToolDeclaration structural integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestToolDeclarationStructure:
    """Every declaration must satisfy the structural contract."""

    REQUIRED_KEYS = {"name", "description", "parameters"}
    VALID_ROUTING_ARG_TOKENS = {"$user_input"}

    @pytest.mark.parametrize("decl", TOOL_DECLARATIONS, ids=[d["name"] for d in TOOL_DECLARATIONS])
    def test_required_keys_present(self, decl):
        missing = self.REQUIRED_KEYS - decl.keys()
        assert not missing, f"Tool '{decl['name']}' missing keys: {missing}"

    @pytest.mark.parametrize("decl", TOOL_DECLARATIONS, ids=[d["name"] for d in TOOL_DECLARATIONS])
    def test_name_is_nonempty_string(self, decl):
        assert isinstance(decl["name"], str) and decl["name"].strip()

    @pytest.mark.parametrize("decl", TOOL_DECLARATIONS, ids=[d["name"] for d in TOOL_DECLARATIONS])
    def test_description_is_nonempty_string(self, decl):
        assert isinstance(decl["description"], str) and decl["description"].strip()

    @pytest.mark.parametrize("decl", TOOL_DECLARATIONS, ids=[d["name"] for d in TOOL_DECLARATIONS])
    def test_parameters_has_required_list(self, decl):
        params = decl["parameters"]
        assert "required" in params, f"Tool '{decl['name']}' parameters missing 'required' list"
        assert isinstance(params["required"], list)
        assert len(params["required"]) >= 1

    def test_tool_names_are_unique(self):
        names = [d["name"] for d in TOOL_DECLARATIONS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    # ── Routing field integrity ───────────────────────────────────────────────

    @pytest.mark.parametrize(
        "decl",
        [d for d in TOOL_DECLARATIONS if "routing" in d],
        ids=[d["name"] for d in TOOL_DECLARATIONS if "routing" in d],
    )
    def test_routing_force_when_is_list(self, decl):
        routing = decl["routing"]
        assert "force_when" in routing, f"Tool '{decl['name']}' routing missing 'force_when'"
        assert isinstance(routing["force_when"], list)

    @pytest.mark.parametrize(
        "decl",
        [d for d in TOOL_DECLARATIONS if "routing" in d],
        ids=[d["name"] for d in TOOL_DECLARATIONS if "routing" in d],
    )
    def test_routing_force_when_entries_have_required_keys(self, decl):
        for i, cond in enumerate(decl["routing"]["force_when"]):
            has_state_key = "state_key" in cond
            has_input_trigger = "input_contains_any" in cond
            assert has_state_key or has_input_trigger, (
                f"Tool '{decl['name']}' force_when[{i}] must have at least one of "
                f"'state_key' or 'input_contains_any'"
            )
            assert "reason" in cond, (
                f"Tool '{decl['name']}' force_when[{i}] missing 'reason'"
            )
            assert isinstance(cond["reason"], str) and cond["reason"].strip(), (
                f"Tool '{decl['name']}' force_when[{i}] reason must be a nonempty string"
            )
            if has_input_trigger:
                assert isinstance(cond["input_contains_any"], list) and cond["input_contains_any"], (
                    f"Tool '{decl['name']}' force_when[{i}] input_contains_any must be a nonempty list"
                )

    @pytest.mark.parametrize(
        "decl",
        [d for d in TOOL_DECLARATIONS if "routing" in d],
        ids=[d["name"] for d in TOOL_DECLARATIONS if "routing" in d],
    )
    def test_routing_args_use_valid_tokens(self, decl):
        for i, cond in enumerate(decl["routing"]["force_when"]):
            for arg_key, arg_val in (cond.get("args") or {}).items():
                if isinstance(arg_val, str) and arg_val.startswith("$"):
                    assert arg_val in self.VALID_ROUTING_ARG_TOKENS, (
                        f"Tool '{decl['name']}' force_when[{i}] args.{arg_key} "
                        f"uses unknown token '{arg_val}'. "
                        f"Valid tokens: {self.VALID_ROUTING_ARG_TOKENS}"
                    )

    @pytest.mark.parametrize(
        "decl",
        [d for d in TOOL_DECLARATIONS if "routing" in d],
        ids=[d["name"] for d in TOOL_DECLARATIONS if "routing" in d],
    )
    def test_routing_args_are_subset_of_declared_parameters(self, decl):
        declared_params = set(decl["parameters"].get("properties", {}).keys())
        for i, cond in enumerate(decl["routing"]["force_when"]):
            for arg_key in (cond.get("args") or {}).keys():
                assert arg_key in declared_params, (
                    f"Tool '{decl['name']}' force_when[{i}] passes arg '{arg_key}' "
                    f"which is not declared in parameters.properties: {declared_params}"
                )

    @pytest.mark.parametrize(
        "decl",
        [d for d in TOOL_DECLARATIONS if "routing" in d],
        ids=[d["name"] for d in TOOL_DECLARATIONS if "routing" in d],
    )
    def test_routing_required_args_covered(self, decl):
        """force_when args must supply all required parameters."""
        required = set(decl["parameters"].get("required", []))
        for i, cond in enumerate(decl["routing"]["force_when"]):
            provided = set((cond.get("args") or {}).keys())
            missing = required - provided
            assert not missing, (
                f"Tool '{decl['name']}' force_when[{i}] does not supply required args: {missing}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. RoutingGuide — force_when evaluation
# ─────────────────────────────────────────────────────────────────────────────

class TestRoutingGuide:

    def test_no_match_on_empty_state(self, guide, empty_state):
        result = guide.evaluate("帮我想想这个星期吃什么", empty_state)
        assert result is None, "Guide must not fire when state is empty"

    def test_no_match_when_only_pending_queue_set(self, guide):
        state = {"pending_planning_queue": [{"date": "2026-05-05"}]}
        result = guide.evaluate("好的", state)
        assert result is None, (
            "Guide must not fire when pending_planning_queue is set "
            "but last_pipeline_action != 'ask_confirm_dates'"
        )

    def test_no_match_when_only_action_set(self, guide):
        state = {"last_pipeline_action": "ask_confirm_dates"}
        result = guide.evaluate("好的", state)
        assert result is None, (
            "Guide must not fire when last_pipeline_action matches "
            "but pending_planning_queue is absent/empty"
        )

    def test_fires_plan_meal_on_pending_confirm(self, guide, pending_confirm_state):
        result = guide.evaluate("好的", pending_confirm_state)
        assert result is not None
        assert result.tool == "plan_meal"

    def test_user_input_substituted_into_args(self, guide, pending_confirm_state):
        user_msg = "确认这周的安排"
        result = guide.evaluate(user_msg, pending_confirm_state)
        assert result.args["user_request"] == user_msg

    def test_reason_is_nonempty(self, guide, pending_confirm_state):
        result = guide.evaluate("ok", pending_confirm_state)
        assert result.reason and isinstance(result.reason, str)

    @pytest.mark.parametrize("affirmation", ["好的", "确认", "ok", "yes", "可以", "取消", "cancel", "对", "不行"])
    def test_all_affirmations_are_routed(self, guide, pending_confirm_state, affirmation):
        """Even single-character replies must trigger the guide."""
        result = guide.evaluate(affirmation, pending_confirm_state)
        assert result is not None and result.tool == "plan_meal", (
            f"Affirmation '{affirmation}' was not routed to plan_meal"
        )

    def test_general_meal_question_not_hard_routed(self, guide):
        """'帮我想想吃什么' with no pending state must go to LLM, not guide."""
        result = guide.evaluate("帮我想想这个星期吃什么", {})
        assert result is None

    def test_pending_options_routes_to_plan_meal(self, guide):
        """When pending_options is set (user is choosing between options), guide must fire."""
        state = {"pending_options": [{"name": "方案一"}, {"name": "方案二"}]}
        result = guide.evaluate("选方案二", state)
        assert result is not None and result.tool == "plan_meal", (
            "pending_options must hard-route to plan_meal to apply the selection"
        )

    def test_meal_planning_queue_routes_to_plan_meal(self, guide):
        """When meal_planning_queue is active (day-by-day mode), guide must fire."""
        state = {"meal_planning_queue": ["2026-05-03|dinner", "2026-05-04|dinner"]}
        result = guide.evaluate("好的继续", state)
        assert result is not None and result.tool == "plan_meal", (
            "active meal_planning_queue must hard-route to plan_meal to continue queue"
        )



# ─────────────────────────────────────────────────────────────────────────────
# 3. ResponseSensor — anomaly detection and auto-correction
# ─────────────────────────────────────────────────────────────────────────────

class TestResponseSensor:

    def _make_fc_part(self, name: str, args: Dict) -> Dict:
        return {"functionCall": {"name": name, "args": args}}

    def _make_text_part(self, text: str) -> Dict:
        return {"text": text}

    # ── Clean responses ───────────────────────────────────────────────────────

    def test_clean_function_call_no_anomaly(self, sensor):
        parts = [self._make_fc_part("plan_meal", {"user_request": "帮我规划"})]
        result = sensor.inspect(parts, "帮我规划")
        assert not result.anomalies
        assert len(result.function_calls) == 1
        assert result.text == ""

    def test_clean_text_response_no_anomaly(self, sensor):
        parts = [self._make_text_part("好的，本周推荐以下菜单…")]
        result = sensor.inspect(parts, "帮我想想")
        assert not result.anomalies
        assert result.function_calls == []
        assert "好的" in result.text

    # ── Mixed response ────────────────────────────────────────────────────────

    def test_detects_mixed_response(self, sensor):
        parts = [
            self._make_text_part("好的，正在生成餐单。"),
            self._make_fc_part("plan_meal", {"user_request": "帮我规划"}),
        ]
        result = sensor.inspect(parts, "帮我规划")
        kinds = [a.kind for a in result.anomalies]
        assert AnomalyKind.MIXED_RESPONSE in kinds

    def test_mixed_response_text_is_discarded(self, sensor):
        parts = [
            self._make_text_part("好的，正在生成餐单。"),
            self._make_fc_part("plan_meal", {"user_request": "帮我规划"}),
        ]
        result = sensor.inspect(parts, "帮我规划")
        assert result.text == "", "Spurious text must be discarded on mixed response"
        assert len(result.function_calls) == 1

    def test_mixed_response_function_call_preserved(self, sensor):
        parts = [
            self._make_text_part("请稍等"),
            self._make_fc_part("search_items", {"query": "鸡蛋"}),
        ]
        result = sensor.inspect(parts, "找鸡蛋")
        assert result.function_calls[0]["name"] == "search_items"

    # ── Unknown tool ──────────────────────────────────────────────────────────

    def test_detects_unknown_tool(self, sensor):
        parts = [self._make_fc_part("get_meal_plan", {"num_days": 7})]
        result = sensor.inspect(parts, "帮我规划一周")
        kinds = [a.kind for a in result.anomalies]
        assert AnomalyKind.UNKNOWN_TOOL in kinds

    def test_unknown_tool_call_is_dropped(self, sensor):
        parts = [self._make_fc_part("get_meal_plan", {"num_days": 7})]
        result = sensor.inspect(parts, "帮我规划一周")
        assert result.function_calls == []

    # ── Missing required args ─────────────────────────────────────────────────

    def test_detects_missing_required_args(self, sensor):
        parts = [self._make_fc_part("plan_meal", {})]  # user_request required
        result = sensor.inspect(parts, "帮我规划")
        kinds = [a.kind for a in result.anomalies]
        assert AnomalyKind.MISSING_ARGS in kinds

    def test_missing_args_call_is_kept(self, sensor):
        """Incomplete calls are flagged but not dropped — executor may handle partial args."""
        parts = [self._make_fc_part("plan_meal", {})]
        result = sensor.inspect(parts, "帮我规划")
        assert len(result.function_calls) == 1

    # ── Empty response ────────────────────────────────────────────────────────

    def test_detects_empty_response(self, sensor):
        result = sensor.inspect([], "你好")
        kinds = [a.kind for a in result.anomalies]
        assert AnomalyKind.EMPTY_RESPONSE in kinds

    def test_thought_parts_ignored(self, sensor):
        """Parts with 'thought': true must be treated as absent."""
        parts = [{"text": "thinking…", "thought": True}]
        result = sensor.inspect(parts, "你好")
        assert AnomalyKind.EMPTY_RESPONSE in [a.kind for a in result.anomalies]
        assert result.text == ""

    # ── Sensor derives known-tool list from registry ──────────────────────────

    def test_sensor_knows_all_declared_tools(self, sensor):
        declared_names = {d["name"] for d in TOOL_DECLARATIONS}
        assert set(sensor._known.keys()) == declared_names, (
            "ResponseSensor._known must exactly match TOOL_DECLARATIONS names. "
            "Missing or extra entries indicate a sync problem."
        )

    @pytest.mark.parametrize("decl", TOOL_DECLARATIONS, ids=[d["name"] for d in TOOL_DECLARATIONS])
    def test_sensor_required_args_match_declaration(self, sensor, decl):
        declared_required = set(decl["parameters"].get("required", []))
        sensor_required = set(sensor._known[decl["name"]])
        assert sensor_required == declared_required, (
            f"Sensor required args for '{decl['name']}' ({sensor_required}) "
            f"do not match declaration ({declared_required})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Clarification behaviour — which anomalies surface to user vs. auto-correct
# ─────────────────────────────────────────────────────────────────────────────

class TestClarificationBehaviour:
    """
    MIXED_RESPONSE is an internal LLM quirk — users should not see it.
    UNKNOWN_TOOL and MISSING_ARGS represent unclear user intent and must produce a
    clarification_hint so the pipeline can ask the user.
    EMPTY_RESPONSE is intentionally excluded from CLARIFICATION_NEEDED: the chat.py
    plain_generate fallback already produces a natural helpful response without forcing
    a clarifying question (which would disrupt the conversation context).
    """

    def _make_fc_part(self, name: str, args: Dict) -> Dict:
        return {"functionCall": {"name": name, "args": args}}

    def _make_text_part(self, text: str) -> Dict:
        return {"text": text}

    # ── mixed_response: auto-correct, do NOT ask user ─────────────────────────

    def test_mixed_response_no_clarification_hint(self, sensor):
        parts = [
            self._make_text_part("好的，正在生成餐单。"),
            self._make_fc_part("plan_meal", {"user_request": "帮我规划"}),
        ]
        result = sensor.inspect(parts, "帮我规划")
        assert result.clarification_hint is None, (
            "MIXED_RESPONSE is auto-correctable — must not produce a clarification_hint"
        )

    def test_mixed_response_not_in_clarification_needed(self):
        assert AnomalyKind.MIXED_RESPONSE not in CLARIFICATION_NEEDED

    # ── unknown_tool: user intent unclear — must produce hint ─────────────────

    def test_unknown_tool_produces_clarification_hint(self, sensor):
        parts = [self._make_fc_part("get_meal_plan", {"num_days": 7})]
        result = sensor.inspect(parts, "帮我规划一周")
        assert result.clarification_hint is not None, (
            "UNKNOWN_TOOL must produce a clarification_hint so the pipeline asks the user"
        )

    def test_unknown_tool_hint_is_nonempty_string(self, sensor):
        parts = [self._make_fc_part("get_meal_plan", {"num_days": 7})]
        result = sensor.inspect(parts, "帮我规划一周")
        assert isinstance(result.clarification_hint, str) and result.clarification_hint.strip()

    # ── missing_args: AI understood intent but lacks info — must produce hint ──

    def test_missing_args_produces_clarification_hint(self, sensor):
        parts = [self._make_fc_part("plan_meal", {})]
        result = sensor.inspect(parts, "帮我规划")
        assert result.clarification_hint is not None, (
            "MISSING_ARGS must produce a clarification_hint"
        )

    # ── empty_response: auto-handle via fallback, must NOT produce hint ─────────

    def test_empty_response_no_clarification_hint(self, sensor):
        result = sensor.inspect([], "你好")
        assert result.clarification_hint is None, (
            "EMPTY_RESPONSE must NOT produce a clarification_hint — "
            "chat.py plain_generate fallback handles it naturally"
        )

    def test_empty_response_not_in_clarification_needed(self):
        assert AnomalyKind.EMPTY_RESPONSE not in CLARIFICATION_NEEDED

    # ── clean responses: no anomaly → no hint ─────────────────────────────────

    def test_clean_function_call_no_hint(self, sensor):
        parts = [self._make_fc_part("plan_meal", {"user_request": "帮我规划"})]
        result = sensor.inspect(parts, "帮我规划")
        assert result.clarification_hint is None

    def test_clean_text_no_hint(self, sensor):
        parts = [self._make_text_part("好的，本周推荐…")]
        result = sensor.inspect(parts, "帮我想想")
        assert result.clarification_hint is None

    # ── CLARIFICATION_HINTS coverage ──────────────────────────────────────────

    def test_every_clarification_needed_kind_has_hint(self):
        for kind in CLARIFICATION_NEEDED:
            assert kind in CLARIFICATION_HINTS, (
                f"AnomalyKind '{kind}' is in CLARIFICATION_NEEDED but has no entry "
                f"in CLARIFICATION_HINTS. Add a hint so the LLM knows what to ask."
            )

    def test_all_hints_are_nonempty_strings(self):
        for kind, hint in CLARIFICATION_HINTS.items():
            assert isinstance(hint, str) and hint.strip(), (
                f"CLARIFICATION_HINTS['{kind}'] must be a nonempty string"
            )
