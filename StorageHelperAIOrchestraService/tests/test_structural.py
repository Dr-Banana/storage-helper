"""
Structural tests — no LLM calls, no network, no DB.
Verify imports, file layout, and schema shapes for the current architecture.
"""
from pathlib import Path

SKILLS_ROOT = Path(__file__).parent.parent / "app" / "skills" / "meal_planning"


# ── Imports ───────────────────────────────────────────────────────────────────

def test_meal_planning_agent_imports():
    from app.agents.meal_planning_agent import MealPlanningAgent  # noqa: F401


def test_schedule_commands_imports():
    from app.db import schedule_commands  # noqa: F401


def test_api_schemas_imports():
    from app.api.schemas import ChatRequest, ChatResponse, ChatMessage  # noqa: F401


def test_router_imports():
    from app.api.router import api_router  # noqa: F401


# ── MealPlanningAgent constructor ─────────────────────────────────────────────

def test_agent_constructor_accepts_expected_params():
    from app.agents.meal_planning_agent import MealPlanningAgent
    agent = MealPlanningAgent(
        auth_token="tok",
        cooking_level="intermediate",
        language="zh-CN",
        user_timezone="Asia/Shanghai",
    )
    assert agent.auth_token == "tok"
    assert agent.cooking_level == "intermediate"
    assert agent.language == "zh-CN"
    assert agent.user_timezone == "Asia/Shanghai"


def test_agent_has_no_stateful_phase():
    """New agent is stateless — no Phase enum, no _state attribute."""
    import inspect
    from app.agents import meal_planning_agent as m
    assert not hasattr(m, "Phase"), "Phase enum was removed in the openclaw rewrite"
    assert not hasattr(m, "PlanState"), "PlanState was removed in the openclaw rewrite"


# ── Gemini tool declarations ──────────────────────────────────────────────────

def test_tools_use_camelCase_functionDeclarations():
    """
    Regression: using snake_case 'function_declarations' silently breaks Gemini tool
    calling — the key is ignored and no tools are registered.
    """
    from app.agents.meal_planning_agent import _TOOLS
    assert len(_TOOLS) == 1
    tool_group = _TOOLS[0]
    assert "functionDeclarations" in tool_group, (
        "Must use camelCase 'functionDeclarations' for Gemini REST API"
    )
    assert "function_declarations" not in tool_group


def test_tools_declare_expected_functions():
    from app.agents.meal_planning_agent import _TOOLS
    decls = _TOOLS[0]["functionDeclarations"]
    names = {d["name"] for d in decls}
    assert names == {
        "fetch_meal_plan",
        "save_meal_plan",
        "delete_meal_plan",
        "suggest_todays_menu",
        "get_recipe_details",
        "get_recipes_by_category",
        "recommend_weekly_meals",
    }


def test_save_meal_plan_dish_name_is_only_required_field():
    """
    Two-phase save: ingredients and steps are optional per dish so the model can
    call save_meal_plan with names only in phase 1.
    """
    from app.agents.meal_planning_agent import _TOOLS
    decls = _TOOLS[0]["functionDeclarations"]
    save_decl = next(d for d in decls if d["name"] == "save_meal_plan")
    dish_schema = save_decl["parameters"]["properties"]["dishes"]["items"]
    assert dish_schema.get("required") == ["name"], (
        "Only 'name' should be required per dish to allow phase-1 name-only saves"
    )


# ── SKILL.md layout ───────────────────────────────────────────────────────────

def test_unified_skill_md_exists():
    md = SKILLS_ROOT / "SKILL.md"
    assert md.exists(), f"Unified SKILL.md missing at {md}"


def test_skill_md_has_required_sections():
    md = (SKILLS_ROOT / "SKILL.md").read_text(encoding="utf-8")
    for section in ("Date resolution", "Viewing plans", "Planning a new meal", "Modifying an existing plan"):
        assert section in md, f"SKILL.md missing section: {section}"


def test_skill_md_mentions_two_phase_save():
    """Regression: SKILL.md must instruct the two-phase save pattern."""
    md = (SKILLS_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "Phase 1" in md and "Phase 2" in md, (
        "SKILL.md must describe the two-phase save to avoid large LLM responses"
    )


# ── SKILL.md experience guardrails ───────────────────────────────────────────
# Each rule below was added to fix a specific observed failure. Removing the
# rule reintroduces that failure. Wording may evolve, but the anchor phrase
# must survive — or the test must be updated together with a replacement rule.

def _skill_md() -> str:
    return (SKILLS_ROOT / "SKILL.md").read_text(encoding="utf-8")


def test_skill_md_declares_database_as_only_authoritative_source():
    """Failure fixed: AI suggested dishes from its own memory instead of MCP."""
    assert "only authoritative source" in _skill_md()


def test_skill_md_has_data_sourcing_rules():
    md = _skill_md()
    assert "Data sourcing rules" in md
    assert "no exceptions" in md


def test_skill_md_has_try_before_you_refuse_rule():
    """Failure fixed: AI claimed 'no ingredient search' without trying a query."""
    md = _skill_md()
    assert "Try before you refuse" in md
    assert "fuzzy match" in md


def test_skill_md_has_constraints_persist_rule():
    """Failure fixed: 'anything else?' dumped the whole category, forgetting the user had lamb."""
    assert "Constraints persist across turns" in _skill_md()


def test_skill_md_has_curate_dont_dump_rule():
    """Failure fixed: 90-dish category list shown in full."""
    md = _skill_md()
    assert "Curate, don't dump" in md
    assert "5–8" in md or "5-8" in md


def test_skill_md_has_numbered_choice_rule():
    """Failure fixed: '1' resolved against an older list instead of the latest one."""
    md = _skill_md()
    assert "Resolving numbered choices" in md
    assert "MOST RECENTLY" in md


def test_skill_md_has_recipe_adaptation_section():
    """Failure fixed: beef recipe copied verbatim when the user wanted lamb shank."""
    md = _skill_md()
    assert "Adapting a recipe" in md
    assert "database recipe is the base" in md


def test_skill_md_has_worked_examples():
    """Few-shot examples are the main lever for flash tool-use compliance."""
    md = _skill_md()
    assert "Worked examples" in md
    for marker in ("Example 1", "Example 2", "Example 2b", "Example 3", "Example 4"):
        assert marker in md, f"SKILL.md missing worked example: {marker}"
    assert md.count("Wrong:") >= 4, "Examples should state the wrong behavior explicitly"


def test_skill_md_prose_is_english_only():
    """
    Prompt language policy: instructions are pure English. Chinese is allowed
    only inside code fences (functional values like MCP category names).
    """
    import re
    prose = re.sub(r"```.*?```", "", _skill_md(), flags=re.S)
    cjk = re.findall(r"[\u4e00-\u9fff]+", prose)
    assert not cjk, f"Chinese found outside code fences: {cjk[:10]}"


def test_skill_md_no_conflicting_always_first_rules():
    """
    Regression: two global 'Always call X first' rules made Gemini return
    empty responses. At most one tool may be declared a universal first step.
    """
    import re
    hits = re.findall(r"[Aa]lways call `?\w+`? first", _skill_md())
    assert len(hits) <= 1, f"Conflicting 'always first' rules: {hits}"


# ── Agent loop guardrail wiring ───────────────────────────────────────────────
# Behavior is covered in test_meal_planning_agent.py; these assert the wiring
# stays present in the source (cheap tripwire against accidental deletion).

def test_agent_source_keeps_loop_guardrails():
    import inspect
    from app.agents import meal_planning_agent as m
    src = inspect.getsource(m)
    for anchor, why in [
        ("[Self-check", "self-check round when no tools were used"),
        ("[System check", "phase-2 save nudge after action_required"),
        ("interim_text", "fallback to text emitted alongside a tool call"),
        ("MALFORMED_FUNCTION_CALL", "retry with brevity hint"),
        ("thinkingConfig", "explicit thinking budget for tool selection"),
        ("toolConfig", "explicit AUTO function-calling mode"),
        ("[Reminder:", "per-turn data-sourcing reminder"),
    ]:
        assert anchor in src, f"Agent loop guardrail missing: {why} ({anchor})"


# ── API schemas ───────────────────────────────────────────────────────────────

def test_chat_request_schema():
    from app.api.schemas import ChatRequest
    req = ChatRequest(message="hello", owner_id=1)
    assert req.message == "hello"
    assert req.owner_id == 1
    assert req.history == []
    assert req.cooking_level == "beginner"
    assert req.user_timezone is None


def test_chat_response_has_response_field():
    from app.api.schemas import ChatResponse
    resp = ChatResponse(response="hi")
    assert resp.response == "hi"


def test_chat_response_has_no_phase_field():
    """phase was removed from ChatResponse when the state machine was replaced."""
    from app.api.schemas import ChatResponse
    import pydantic
    fields = ChatResponse.model_fields
    assert "phase" not in fields, "ChatResponse.phase was removed; do not re-add it"
