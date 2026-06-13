"""
Structural tests — no LLM calls, no network, no DB.
Verify imports, file layout, and schema shapes.
"""
from pathlib import Path

SKILLS_ROOT = Path(__file__).parent.parent / "app" / "skills" / "meal_planning"


# ── Imports ──────────────────────────────────────────────────────────────────

def test_meal_planning_agent_imports():
    from app.agents.meal_planning_agent import MealPlanningAgent, Phase, PlanState  # noqa: F401


def test_schedule_commands_imports():
    from app.db import schedule_commands  # noqa: F401


def test_skill_runner_imports():
    from app.skills.meal_planning import skill_runner  # noqa: F401


def test_api_schemas_imports():
    from app.api.schemas import ChatRequest, ChatResponse, ChatMessage  # noqa: F401


def test_router_imports():
    from app.api.router import api_router  # noqa: F401


# ── Phase enum ───────────────────────────────────────────────────────────────

def test_phase_enum_values():
    from app.agents.meal_planning_agent import Phase
    assert Phase.GATHER_CONTEXT == "gather_context"
    assert Phase.CLASSIFY_DISHES == "classify_dishes"
    assert Phase.GENERATE_STEPS == "generate_steps"
    assert Phase.SAVE == "save"
    assert Phase.DONE == "done"
    assert Phase.CLARIFY_INTENT == "clarify_intent"


# ── PlanState defaults ────────────────────────────────────────────────────────

def test_plan_state_defaults():
    from app.agents.meal_planning_agent import PlanState, Phase
    s = PlanState()
    assert s.phase == Phase.GATHER_CONTEXT
    assert s.date is None
    assert s.meal_type is None
    assert s.dishes == []
    assert s.dishes_with_steps == []
    assert s.saved_schedule_id is None


# ── SKILL.md files exist ──────────────────────────────────────────────────────

def test_skill_md_files_exist():
    for skill in ("gather_context", "classify_dishes", "generate_cooking_steps"):
        md = SKILLS_ROOT / skill / "SKILL.md"
        assert md.exists(), f"Missing {md}"


def test_skill_md_frontmatter_parseable():
    from app.skills.meal_planning.skill_runner import _load_skill
    for skill in ("gather_context", "classify_dishes", "generate_cooking_steps"):
        result = _load_skill(SKILLS_ROOT / skill)
        assert "meta" in result
        assert "prompt" in result
        assert result["prompt"]  # non-empty


def test_skill_md_has_required_meta_keys():
    from app.skills.meal_planning.skill_runner import _load_skill
    for skill in ("gather_context", "classify_dishes", "generate_cooking_steps"):
        meta = _load_skill(SKILLS_ROOT / skill)["meta"]
        assert "temperature" in meta, f"{skill} missing temperature"
        assert "max_tokens" in meta, f"{skill} missing max_tokens"
        assert isinstance(meta["temperature"], float)
        assert isinstance(meta["max_tokens"], int)


def test_classify_dishes_max_tokens_sufficient():
    """classify_dishes was bumped to 800 to avoid JSON truncation."""
    from app.skills.meal_planning.skill_runner import _load_skill
    meta = _load_skill(SKILLS_ROOT / "classify_dishes")["meta"]
    assert meta["max_tokens"] >= 800


def test_generate_cooking_steps_max_tokens_sufficient():
    from app.skills.meal_planning.skill_runner import _load_skill
    meta = _load_skill(SKILLS_ROOT / "generate_cooking_steps")["meta"]
    assert meta["max_tokens"] >= 2000


# ── API schemas ───────────────────────────────────────────────────────────────

def test_chat_request_schema():
    from app.api.schemas import ChatRequest
    req = ChatRequest(message="hello", owner_id=1)
    assert req.message == "hello"
    assert req.owner_id == 1
    assert req.history == []
    assert req.cooking_level == "beginner"
    assert req.user_timezone is None


def test_chat_response_schema():
    from app.api.schemas import ChatResponse
    resp = ChatResponse(response="hi", phase="gather_context")
    assert resp.response == "hi"
    assert resp.phase == "gather_context"
