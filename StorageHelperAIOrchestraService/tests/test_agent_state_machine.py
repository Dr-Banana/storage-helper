"""
State machine tests for MealPlanningAgent — LLM calls mocked via unittest.mock.
"""
import pytest
from unittest.mock import AsyncMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_agent():
    from app.agents.meal_planning_agent import MealPlanningAgent
    return MealPlanningAgent(
        gemini_api_url="http://mock",
        auth_token="token",
        cooking_level="beginner",
    )


# ── reset ─────────────────────────────────────────────────────────────────────

def test_reset_clears_state():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()
    agent._state.phase = Phase.DONE
    agent._state.date = "2026-06-13"
    agent._state.dishes = [{"name": "X"}]
    agent.reset()
    assert agent._state.phase == Phase.GATHER_CONTEXT
    assert agent._state.date is None
    assert agent._state.dishes == []


# ── gather_context ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_context_incomplete_returns_question():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()

    skill_result = {
        "confirmed": False,
        "context": {"date": None, "meal_type": "dinner"},
        "question": "Which day are you planning for dinner?",
    }
    with patch(
        "app.agents.meal_planning_agent.skill_runner.run",
        new=AsyncMock(return_value=skill_result),
    ):
        reply = await agent.run("晚饭", [], user_timezone="Asia/Shanghai")

    assert "dinner" in reply.lower() or "day" in reply.lower()
    assert agent._state.phase == Phase.GATHER_CONTEXT


@pytest.mark.asyncio
async def test_gather_context_confirmed_advances_to_classify():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()

    gather_result = {
        "confirmed": True,
        "context": {"date": "2026-06-13", "meal_type": "dinner"},
        "question": None,
    }
    classify_result = {
        "stage": "collecting",
        "action": "add",
        "replaces": None,
        "dishes": [],
        "question": "What would you like to eat?",
        "suggestion_text": None,
    }

    call_count = 0

    async def mock_run(**kwargs):
        nonlocal call_count
        call_count += 1
        return gather_result if call_count == 1 else classify_result

    with patch("app.agents.meal_planning_agent.skill_runner.run", new=mock_run):
        reply = await agent.run("今天晚上", [], user_timezone="Asia/Shanghai")

    assert agent._state.date == "2026-06-13"
    assert agent._state.meal_type == "dinner"


# ── classify_dishes ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_dishes_suggesting_stores_dishes():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()
    agent._state.phase = Phase.CLASSIFY_DISHES

    skill_result = {
        "stage": "suggesting",
        "action": "add",
        "replaces": None,
        "dishes": [{"name": "Pork Ribs", "style": "braised", "flavor": "savory"}],
        "question": None,
        "suggestion_text": "How about Braised Pork Ribs?",
    }
    with patch(
        "app.agents.meal_planning_agent.skill_runner.run",
        new=AsyncMock(return_value=skill_result),
    ):
        reply = await agent.run("猪肋排", [])

    assert agent._state.dishes == [{"name": "Pork Ribs", "style": "braised", "flavor": "savory"}]
    assert agent._state.phase == Phase.CLASSIFY_DISHES
    assert "Braised Pork Ribs" in reply


@pytest.mark.asyncio
async def test_classify_dishes_confirmed_chains_to_generate_steps():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()
    agent._state.phase = Phase.CLASSIFY_DISHES
    agent._state.date = "2026-06-13"
    agent._state.meal_type = "dinner"

    classify_result = {
        "stage": "confirmed",
        "action": "add",
        "replaces": None,
        "dishes": [{"name": "Pork Ribs", "style": "braised", "flavor": "savory"}],
        "question": None,
        "suggestion_text": None,
    }
    generate_result = {
        "dishes": [
            {
                "name": "Pork Ribs",
                "ingredients": [{"name": "pork ribs", "quantity": "500g"}],
                "steps": ["Step 1"],
            }
        ]
    }

    call_count = 0

    async def mock_run(**kwargs):
        nonlocal call_count
        call_count += 1
        return classify_result if call_count == 1 else generate_result

    with patch(
        "app.agents.meal_planning_agent.skill_runner.run",
        new=mock_run,
    ), patch(
        "app.agents.meal_planning_agent.schedule_commands.fetch_existing",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.agents.meal_planning_agent.schedule_commands.save_plan",
        new=AsyncMock(return_value={"id": 99}),
    ):
        reply = await agent.run("可以", [])

    assert agent._state.phase == Phase.DONE
    assert agent._state.saved_schedule_id == 99
    assert "Pork Ribs" in reply


# ── DONE → CLARIFY_INTENT ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_done_phase_shows_current_plan_and_asks_intent():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()
    agent._state.phase = Phase.DONE
    agent._state.date = "2026-06-13"
    agent._state.meal_type = "dinner"
    agent._state.saved_schedule_id = 99
    agent._state.dishes_with_steps = [{"name": "Pork Ribs", "ingredients": [], "steps": []}]

    reply = await agent.run("想改一下", [])

    assert "Pork Ribs" in reply
    assert agent._state.phase == Phase.CLARIFY_INTENT


# ── replace action ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replace_action_removes_old_dish():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()
    agent._state.phase = Phase.CLARIFY_INTENT
    agent._state.date = "2026-06-13"
    agent._state.meal_type = "dinner"
    agent._state.saved_schedule_id = 99
    agent._state.dishes_with_steps = [
        {"name": "Pork Ribs", "ingredients": [], "steps": []}
    ]

    classify_result = {
        "stage": "confirmed",
        "action": "replace",
        "replaces": "Pork Ribs",
        "dishes": [{"name": "Braised Pork Ribs", "style": "braised", "flavor": "savory"}],
        "question": None,
        "suggestion_text": None,
    }
    generate_result = {
        "dishes": [
            {
                "name": "Braised Pork Ribs",
                "ingredients": [{"name": "pork ribs", "quantity": "500g"}],
                "steps": ["Step 1"],
            }
        ]
    }

    call_count = 0

    async def mock_run(**kwargs):
        nonlocal call_count
        call_count += 1
        return classify_result if call_count == 1 else generate_result

    with patch(
        "app.agents.meal_planning_agent.skill_runner.run",
        new=mock_run,
    ), patch(
        "app.agents.meal_planning_agent.schedule_commands.fetch_existing",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.agents.meal_planning_agent.schedule_commands.save_plan",
        new=AsyncMock(return_value={"id": 99}),
    ):
        reply = await agent.run("改成红烧的", [])

    assert agent._state.phase == Phase.DONE
    final_names = [d["name"] for d in agent._state.dishes_with_steps]
    assert "Braised Pork Ribs" in final_names
    assert "Pork Ribs" not in final_names


# ── date staleness auto-reset ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_date_triggers_reset():
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()
    agent._state.phase = Phase.DONE
    agent._state.date = "2020-01-01"  # far in the past
    agent._state.saved_schedule_id = 5

    gather_result = {
        "confirmed": False,
        "context": {"date": None, "meal_type": None},
        "question": "Which day and meal?",
    }
    with patch(
        "app.agents.meal_planning_agent.skill_runner.run",
        new=AsyncMock(return_value=gather_result),
    ):
        reply = await agent.run("今天晚上", [], user_timezone="Asia/Shanghai")

    assert agent._state.phase == Phase.GATHER_CONTEXT


# ── no-plan prefix during initial flow ───────────────────────────────────────

@pytest.mark.asyncio
async def test_no_already_in_plan_prefix_during_initial_flow():
    """[Already in plan:] must NOT be injected before the plan is saved."""
    from app.agents.meal_planning_agent import Phase
    agent = make_agent()
    agent._state.phase = Phase.CLASSIFY_DISHES
    # No saved_schedule_id → initial flow

    captured = {}

    async def mock_run(skill_dir, user_message, **kwargs):
        captured["user_message"] = user_message
        return {
            "stage": "collecting",
            "action": "add",
            "replaces": None,
            "dishes": [],
            "question": "What would you like?",
            "suggestion_text": None,
        }

    with patch("app.agents.meal_planning_agent.skill_runner.run", new=mock_run):
        await agent.run("猪肋排", [])

    assert not captured["user_message"].startswith("[Already in plan:")
