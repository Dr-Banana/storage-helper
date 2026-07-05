"""
Unit tests for MealPlanningAgent (openclaw-style agent loop).
Gemini API mocked via _call_gemini; schedule_commands mocked via unittest.mock.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Test helpers ──────────────────────────────────────────────────────────────

def _gemini_text(text: str) -> dict:
    return {
        "candidates": [{
            "content": {"role": "model", "parts": [{"text": text}]},
            "finishReason": "STOP",
        }]
    }


def _gemini_tool_call(name: str, args: dict) -> dict:
    return {
        "candidates": [{
            "content": {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}}]},
            "finishReason": "STOP",
        }]
    }


def _gemini_empty() -> dict:
    return {
        "candidates": [{
            "content": {"role": "model", "parts": []},
            "finishReason": "STOP",
        }]
    }


def _sample_record(date: str, meal_type: str, dishes: list) -> dict:
    from app.db.schedule_commands import _build_metadata
    return {"id": 42, "metadata": _build_metadata(date, meal_type, dishes)}


def make_agent(**kwargs):
    from app.agents.meal_planning_agent import MealPlanningAgent
    return MealPlanningAgent(
        auth_token=kwargs.get("auth_token", "test-token"),
        cooking_level=kwargs.get("cooking_level", "beginner"),
        language=kwargs.get("language", "en-US"),
        user_timezone=kwargs.get("user_timezone", "Asia/Shanghai"),
    )


# ── Agent loop — basic behavior ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_only_response_returned_immediately():
    """No tool calls → return the text (after one self-check round)."""
    agent = make_agent()
    with patch.object(agent, "_call_gemini", new=AsyncMock(return_value=_gemini_text("Noodles tonight"))):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")
    assert reply == "Noodles tonight"


@pytest.mark.asyncio
async def test_single_tool_call_then_text():
    """fetch call → result injected → Gemini returns final text."""
    agent = make_agent()
    responses = iter([
        _gemini_tool_call("fetch_meal_plan", {"date": "2026-06-14", "meal_type": "dinner"}),
        _gemini_text("Tonight you have Zucchini Egg Soup"),
    ])
    with patch.object(agent, "_call_gemini", new=AsyncMock(side_effect=lambda *a, **kw: next(responses))), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")
    assert reply == "Tonight you have Zucchini Egg Soup"


@pytest.mark.asyncio
async def test_fetch_save_text_three_round_flow():
    """Two tool calls (fetch → save) then final text — models the two-phase save."""
    agent = make_agent()
    responses = iter([
        _gemini_tool_call("fetch_meal_plan", {"date": "2026-06-14", "meal_type": "dinner"}),
        _gemini_tool_call("save_meal_plan", {
            "date": "2026-06-14", "meal_type": "dinner",
            "dishes": [{"name": "Noodles", "ingredients": [], "steps": ["Boil"]}],
        }),
        _gemini_text("Saved noodles for tonight!"),
    ])
    with patch.object(agent, "_call_gemini", new=AsyncMock(side_effect=lambda *a, **kw: next(responses))), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan",
               new=AsyncMock(return_value={"id": 99})):
        reply = await agent.run("Plan my dinner tonight", [], user_timezone="Asia/Shanghai")
    assert reply == "Saved noodles for tonight!"


@pytest.mark.asyncio
async def test_max_rounds_returns_fallback():
    """After 10 rounds of tool calls, return a fallback message."""
    agent = make_agent(language="en-US")
    with patch.object(agent, "_call_gemini",
                      new=AsyncMock(return_value=_gemini_tool_call("fetch_meal_plan", {"date": "2026-06-14"}))), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_upcoming",
               new=AsyncMock(return_value=[])):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")
    assert "try again" in reply.lower()


@pytest.mark.asyncio
async def test_max_rounds_fallback_localized_for_chinese_users():
    """language=zh-CN users get the Chinese fallback message."""
    agent = make_agent(language="zh-CN")
    with patch.object(agent, "_call_gemini",
                      new=AsyncMock(return_value=_gemini_tool_call("fetch_meal_plan", {"date": "2026-06-14"}))), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_upcoming",
               new=AsyncMock(return_value=[])):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")
    assert reply and "try again" not in reply.lower(), "zh users must get the localized fallback"


@pytest.mark.asyncio
async def test_empty_gemini_response_returns_non_empty_fallback():
    """
    Regression: empty parts used to return '' silently.
    Now returns a user-visible error message.
    """
    agent = make_agent(language="en-US")
    with patch.object(agent, "_call_gemini", new=AsyncMock(return_value=_gemini_empty())):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")
    assert reply, "Empty Gemini response must produce a non-empty fallback, not silent ''"
    assert "try again" in reply.lower()


@pytest.mark.asyncio
async def test_on_text_callback_called_with_final_text():
    """on_text streaming callback fires with the final reply."""
    agent = make_agent()
    collected = []
    with patch.object(agent, "_call_gemini", new=AsyncMock(return_value=_gemini_text("reply content"))):
        await agent.run("question", [], on_text=collected.append)
    assert collected == ["reply content"]


# ── _tool_fetch ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_with_meal_type_calls_fetch_existing():
    agent = make_agent()
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)) as mock_fe:
        result = await agent._tool_fetch({"date": "2026-06-14", "meal_type": "dinner"})
    mock_fe.assert_called_once_with("2026-06-14", "dinner", "test-token")
    assert result == {"date": "2026-06-14", "meal_type": "dinner", "found": False}


@pytest.mark.asyncio
async def test_fetch_with_meal_type_returns_dishes():
    agent = make_agent()
    record = _sample_record("2026-06-14", "dinner", [
        {"name": "Zucchini Egg Soup", "ingredients": [{"name": "zucchini", "quantity": "1"}], "steps": ["step1"]}
    ])
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=record)):
        result = await agent._tool_fetch({"date": "2026-06-14", "meal_type": "dinner"})
    assert result["found"] is True
    assert result["dishes"][0]["name"] == "Zucchini Egg Soup"


@pytest.mark.asyncio
async def test_fetch_without_meal_type_calls_fetch_upcoming():
    agent = make_agent()
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_upcoming",
               new=AsyncMock(return_value=[])) as mock_fu:
        result = await agent._tool_fetch({"date": "2026-06-14"})
    mock_fu.assert_called_once()
    assert result == {"date": "2026-06-14", "found": False}


@pytest.mark.asyncio
async def test_fetch_without_meal_type_filters_results_by_date():
    """
    fetch_upcoming may return multiple days; _tool_fetch must filter to the
    requested date only — prevents cross-day pollution.
    """
    agent = make_agent()
    summaries = [
        {"date": "2026-06-13", "meal_type": "dinner", "dishes": ["Curry Beef"]},
        {"date": "2026-06-14", "meal_type": "dinner", "dishes": ["Zucchini Egg Soup"]},
    ]
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_upcoming",
               new=AsyncMock(return_value=summaries)):
        result = await agent._tool_fetch({"date": "2026-06-14"})
    assert result["found"] is True
    all_dish_names = [d for m in result["meals"] for d in m["dishes"]]
    assert "Zucchini Egg Soup" in all_dish_names
    assert "Curry Beef" not in all_dish_names


# ── _tool_save: create vs update ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_creates_new_when_no_existing():
    agent = make_agent()
    dishes = [{"name": "Noodles", "ingredients": [{"name": "noodles", "quantity": "100g"}], "steps": ["Boil"]}]
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan",
               new=AsyncMock(return_value={"id": 10})) as mock_save, \
         patch("app.agents.meal_planning_agent.schedule_commands.update_plan") as mock_update:
        result = await agent._tool_save({"date": "2026-06-14", "meal_type": "dinner", "dishes": dishes})
    mock_save.assert_called_once()
    mock_update.assert_not_called()
    assert result == {"success": True, "schedule_id": 10, "saved": {"date": "2026-06-14", "meal_type": "dinner", "dish_count": 1}}


@pytest.mark.asyncio
async def test_save_updates_when_existing_record_found():
    agent = make_agent()
    existing = _sample_record("2026-06-14", "dinner", [
        {"name": "Zucchini Egg Soup", "ingredients": [], "steps": ["step"]}
    ])
    dishes = [
        {"name": "Zucchini Egg Soup"},
        {"name": "Sweet and Sour Pork", "ingredients": [{"name": "pork", "quantity": "200g"}], "steps": ["Fry", "Mix sauce"]},
    ]
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=existing)), \
         patch("app.agents.meal_planning_agent.schedule_commands.update_plan",
               new=AsyncMock(return_value={"id": 42})) as mock_update, \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan") as mock_save:
        result = await agent._tool_save({"date": "2026-06-14", "meal_type": "dinner", "dishes": dishes})
    mock_update.assert_called_once()
    mock_save.assert_not_called()
    assert result["success"] is True
    assert result["saved"]["dish_count"] == 2


@pytest.mark.asyncio
async def test_save_returns_error_when_backend_fails():
    agent = make_agent()
    dishes = [{"name": "Noodles", "ingredients": [], "steps": ["Boil"]}]
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan",
               new=AsyncMock(return_value=None)):
        result = await agent._tool_save({"date": "2026-06-14", "meal_type": "dinner", "dishes": dishes})
    assert result["success"] is False
    assert "error" in result


# ── _tool_save: enrichment (two-phase save regression) ───────────────────────

@pytest.mark.asyncio
async def test_name_only_kept_dish_enriched_from_db():
    """
    Phase-2 save: a kept dish supplied as {"name": "..."} only must have its
    full recipe filled from the DB record, not saved with empty ingredients/steps.
    """
    agent = make_agent()
    stored = [{"name": "Zucchini Egg Soup", "ingredients": [{"name": "zucchini", "quantity": "1"}], "steps": ["step1", "step2"]}]
    existing = _sample_record("2026-06-14", "dinner", stored)

    captured = {}

    async def mock_update(schedule_id, date, meal_type, dishes, auth_token, user_timezone):
        captured["dishes"] = dishes
        return {"id": 42}

    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=existing)), \
         patch("app.agents.meal_planning_agent.schedule_commands.update_plan",
               side_effect=mock_update):
        await agent._tool_save({
            "date": "2026-06-14", "meal_type": "dinner",
            "dishes": [
                {"name": "Zucchini Egg Soup"},  # ← name only, kept dish
                {"name": "Sweet and Sour Pork", "ingredients": [{"name": "pork", "quantity": "200g"}], "steps": ["Fry"]},
            ],
        })

    zucchini = next(d for d in captured["dishes"] if d["name"] == "Zucchini Egg Soup")
    assert zucchini["steps"] == ["step1", "step2"], "Kept dish must be enriched from DB"
    assert zucchini["ingredients"][0]["name"] == "zucchini"

    pork = next(d for d in captured["dishes"] if d["name"] == "Sweet and Sour Pork")
    assert pork["steps"] == ["Fry"], "New dish must keep model-provided data"


@pytest.mark.asyncio
async def test_dish_with_steps_from_model_not_overwritten_by_db():
    """
    When the model provides full steps for a dish that already exists in DB,
    the model's data wins (modify/replace scenario).
    """
    agent = make_agent()
    existing = _sample_record("2026-06-14", "dinner", [
        {"name": "Tomato Scrambled Eggs", "ingredients": [], "steps": ["old step"]}
    ])
    captured = {}

    async def mock_update(schedule_id, date, meal_type, dishes, auth_token, user_timezone):
        captured["dishes"] = dishes
        return {"id": 42}

    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=existing)), \
         patch("app.agents.meal_planning_agent.schedule_commands.update_plan",
               side_effect=mock_update):
        await agent._tool_save({
            "date": "2026-06-14", "meal_type": "dinner",
            "dishes": [{"name": "Tomato Scrambled Eggs", "ingredients": [], "steps": ["new step"]}],
        })

    dish = captured["dishes"][0]
    assert dish["steps"] == ["new step"], "Model-provided steps must not be overwritten by DB"


@pytest.mark.asyncio
async def test_phase1_name_only_new_dish_saved_as_is():
    """
    Phase-1 save: a brand-new dish with no steps (first save, name only) is saved
    with empty steps — the DB has nothing to enrich from.
    """
    agent = make_agent()
    captured = {}

    async def mock_save(date, meal_type, dishes, auth_token, user_timezone):
        captured["dishes"] = dishes
        return {"id": 5}

    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan",
               side_effect=mock_save):
        await agent._tool_save({
            "date": "2026-06-14", "meal_type": "dinner",
            "dishes": [{"name": "New Dish"}],
        })

    assert captured["dishes"][0]["name"] == "New Dish"
    assert captured["dishes"][0].get("steps", []) == []


# ── _tool_delete ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_found_plan_calls_delete_plan():
    agent = make_agent()
    existing = _sample_record("2026-06-14", "dinner", [])
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=existing)), \
         patch("app.agents.meal_planning_agent.schedule_commands.delete_plan",
               new=AsyncMock(return_value=True)) as mock_del:
        result = await agent._tool_delete({"date": "2026-06-14", "meal_type": "dinner"})
    mock_del.assert_called_once_with(42, "test-token")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_delete_missing_plan_returns_error():
    agent = make_agent()
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)):
        result = await agent._tool_delete({"date": "2026-06-14", "meal_type": "dinner"})
    assert result["success"] is False
    assert "error" in result


# ── unknown tool ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_tool_name_returns_error_dict():
    agent = make_agent()
    result = await agent._execute_tool("nonexistent_tool", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_tool_exception_returns_error_dict():
    agent = make_agent()
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               side_effect=Exception("network error")):
        result = await agent._execute_tool("fetch_meal_plan", {"date": "2026-06-14", "meal_type": "dinner"})
    assert "error" in result


# ── _build_contents ───────────────────────────────────────────────────────────

def test_build_contents_injects_today_and_level():
    agent = make_agent(cooking_level="intermediate")
    contents = agent._build_contents([], "What should I eat tonight?", "2026-06-14")
    last = contents[-1]
    assert last["role"] == "user"
    text = last["parts"][0]["text"]
    assert "[Today: 2026-06-14]" in text
    assert "[Level: intermediate]" in text
    assert "What should I eat tonight?" in text


def test_build_contents_maps_assistant_role_to_model():
    """Gemini requires 'model', not 'assistant', for AI turns."""
    agent = make_agent()
    history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    contents = agent._build_contents(history, "next message", "2026-06-14")
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"
    assert contents[2]["role"] == "user"


def test_build_contents_limits_to_last_10_history_messages():
    agent = make_agent()
    history = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
    contents = agent._build_contents(history, "new", "2026-06-14")
    assert len(contents) == 11  # 10 history + 1 current


def test_build_contents_empty_history():
    agent = make_agent()
    contents = agent._build_contents([], "test", "2026-06-14")
    assert len(contents) == 1
    assert contents[0]["role"] == "user"


# ── reset ────────────────────────────────────────────────────────────────────

def test_reset_is_noop():
    """Agent is stateless; reset() exists for API compatibility and does nothing."""
    agent = make_agent()
    agent.reset()  # must not raise


# ── Agent loop guardrails ─────────────────────────────────────────────────────
# These lock in the experience-critical behaviors added during MCP integration.
# If a prompt or loop change breaks one of these, the user-visible experience
# regresses in a known way — do not delete them to make a change pass.

def _gemini_malformed() -> dict:
    return {
        "candidates": [{
            "content": {"role": "model"},
            "finishReason": "MALFORMED_FUNCTION_CALL",
        }]
    }


def _gemini_text_and_tool(text: str, name: str, args: dict) -> dict:
    """Model emits user-facing text AND a tool call in the same message."""
    return {
        "candidates": [{
            "content": {"role": "model", "parts": [
                {"text": text},
                {"functionCall": {"name": name, "args": args}},
            ]},
            "finishReason": "STOP",
        }]
    }


def _all_injected_texts(recorded):
    texts = []
    for contents in recorded:
        for msg in contents:
            for part in msg.get("parts", []):
                if "text" in part:
                    texts.append(part["text"])
    return texts


@pytest.mark.asyncio
async def test_self_check_fires_when_no_tools_used():
    """
    Guardrail: a text answer produced with zero tool calls triggers one
    self-check round instead of being returned immediately.
    """
    agent = make_agent()
    responses = iter([_gemini_text("answer from memory"), _gemini_text("answer after self-check")])
    recorded = []

    async def scripted(contents, system_prompt):
        recorded.append([m for m in contents])
        return next(responses)

    with patch.object(agent, "_call_gemini", side_effect=scripted):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")

    assert reply == "answer after self-check"
    assert len(recorded) == 2, "Exactly one self-check round expected"
    injected = _all_injected_texts(recorded)
    assert any("[Self-check" in t for t in injected), "Self-check message must be injected"


@pytest.mark.asyncio
async def test_self_check_fires_at_most_once():
    """Self-check must not loop: two consecutive no-tool texts → second is final."""
    agent = make_agent()
    responses = iter([_gemini_text("answer one"), _gemini_text("answer two"), _gemini_text("answer three")])

    async def scripted(contents, system_prompt):
        return next(responses)

    with patch.object(agent, "_call_gemini", side_effect=scripted):
        reply = await agent.run("Hello", [], user_timezone="Asia/Shanghai")

    assert reply == "answer two", "Only one self-check round; second text is final"


@pytest.mark.asyncio
async def test_self_check_skipped_when_tools_were_used():
    """After any tool call, a text response is final — no self-check round."""
    agent = make_agent()
    responses = iter([
        _gemini_tool_call("fetch_meal_plan", {"date": "2026-07-05", "meal_type": "dinner"}),
        _gemini_text("answer after lookup"),
    ])
    calls = []

    async def scripted(contents, system_prompt):
        calls.append(1)
        return next(responses)

    with patch.object(agent, "_call_gemini", side_effect=scripted), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")

    assert reply == "answer after lookup"
    assert len(calls) == 2, "No extra self-check round after tool use"


@pytest.mark.asyncio
async def test_phase2_nudge_forces_second_save():
    """
    Guardrail (regression: dish saved name-only, recipe never persisted):
    save returns action_required → model tries to reply → loop must inject a
    system check and the model must complete the full-recipe save before the
    reply goes out.
    """
    agent = make_agent()
    full_dish = {"name": "Braised Lamb Shank", "ingredients": [{"name": "lamb shank", "quantity": "500g"}], "steps": ["Blanch", "Braise"]}
    responses = iter([
        _gemini_tool_call("save_meal_plan", {
            "date": "2026-07-05", "meal_type": "dinner", "dishes": [{"name": "Braised Lamb Shank"}],
        }),
        _gemini_text("Saved!"),  # premature — phase 2 still owed
        _gemini_tool_call("save_meal_plan", {
            "date": "2026-07-05", "meal_type": "dinner", "dishes": [full_dish],
        }),
        _gemini_text("Saved, recipe below"),
    ])
    recorded = []

    async def scripted(contents, system_prompt):
        recorded.append(list(contents))
        return next(responses)

    save_calls = []

    async def mock_save(date, meal_type, dishes, auth_token, user_timezone):
        save_calls.append(dishes)
        return {"id": 1}

    with patch.object(agent, "_call_gemini", side_effect=scripted), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan",
               side_effect=mock_save):
        reply = await agent.run("Lamb shank it is", [], user_timezone="Asia/Shanghai")

    assert reply == "Saved, recipe below"
    assert len(save_calls) == 2, "Both phase-1 and phase-2 saves must run"
    assert save_calls[1][0]["steps"], "Phase-2 save must contain full steps"
    injected = _all_injected_texts(recorded)
    assert any("[System check" in t for t in injected), "Phase-2 nudge must be injected"


@pytest.mark.asyncio
async def test_phase2_nudge_gives_up_after_two_attempts():
    """The nudge must not loop forever if the model refuses to comply."""
    agent = make_agent()
    responses = iter([
        _gemini_tool_call("save_meal_plan", {
            "date": "2026-07-05", "meal_type": "dinner", "dishes": [{"name": "Braised Lamb Shank"}],
        }),
        _gemini_text("reply one"),
        _gemini_text("reply two"),
        _gemini_text("reply three"),
    ])

    async def scripted(contents, system_prompt):
        return next(responses)

    with patch.object(agent, "_call_gemini", side_effect=scripted), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan",
               new=AsyncMock(return_value={"id": 1})):
        reply = await agent.run("Lamb shank it is", [], user_timezone="Asia/Shanghai")

    assert reply == "reply three", "After 2 nudges the reply must pass through (no infinite loop)"


@pytest.mark.asyncio
async def test_interim_text_used_when_final_response_empty():
    """
    Guardrail (regression: empty reply after text+toolcall message):
    text emitted alongside a tool call must be used as the reply if the
    follow-up model response is empty.
    """
    agent = make_agent()
    responses = iter([
        _gemini_text_and_tool(
            "Saved, recipe below: ...",
            "fetch_meal_plan", {"date": "2026-07-05", "meal_type": "dinner"},
        ),
        _gemini_empty(),
    ])

    async def scripted(contents, system_prompt):
        return next(responses)

    with patch.object(agent, "_call_gemini", side_effect=scripted), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)):
        reply = await agent.run("That one", [], user_timezone="Asia/Shanghai")

    assert reply == "Saved, recipe below: ...", "Interim text must be used, not the apology fallback"


@pytest.mark.asyncio
async def test_malformed_function_call_retried_with_hint():
    """MALFORMED_FUNCTION_CALL → brevity hint injected → retry proceeds."""
    agent = make_agent()
    responses = iter([
        _gemini_malformed(),
        _gemini_tool_call("fetch_meal_plan", {"date": "2026-07-05", "meal_type": "dinner"}),
        _gemini_text("answer after lookup"),
    ])
    recorded = []

    async def scripted(contents, system_prompt):
        recorded.append(list(contents))
        return next(responses)

    with patch.object(agent, "_call_gemini", side_effect=scripted), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)):
        reply = await agent.run("What should I eat tonight?", [], user_timezone="Asia/Shanghai")

    assert reply == "answer after lookup"
    injected = _all_injected_texts(recorded)
    assert any("malformed" in t for t in injected), "Brevity hint must be injected after MALFORMED"


@pytest.mark.asyncio
async def test_list_tool_result_wrapped_in_items_dict():
    """
    Guardrail (regression: HTTP 400 'Proto field is not repeating'):
    a tool returning a list must be wrapped as {"items": [...]} in the
    functionResponse — Gemini rejects bare arrays.
    """
    agent = make_agent()
    responses = iter([
        _gemini_tool_call("get_recipes_by_category", {"category": "meat"}),
        _gemini_text("Here are the meat dishes"),
    ])
    recorded = []

    async def scripted(contents, system_prompt):
        recorded.append([m for m in contents])
        return next(responses)

    with patch.object(agent, "_call_gemini", side_effect=scripted), \
         patch.object(agent, "_execute_tool", new=AsyncMock(return_value=["dish one", "dish two"])):
        reply = await agent.run("What meat dishes are there?", [], user_timezone="Asia/Shanghai")

    assert reply == "Here are the meat dishes"
    fn_responses = [
        part["functionResponse"]["response"]
        for contents in recorded for msg in contents
        for part in msg.get("parts", []) if "functionResponse" in part
    ]
    assert fn_responses, "functionResponse must be present in follow-up contents"
    assert all(isinstance(r, dict) for r in fn_responses), (
        "functionResponse.response must always be a dict, never a bare list"
    )
    assert fn_responses[0] == {"items": ["dish one", "dish two"]}


def test_build_contents_injects_data_sourcing_reminder():
    """Every turn carries the tool-grounding reminder next to [Today]."""
    agent = make_agent()
    contents = agent._build_contents([], "What should I eat tonight?", "2026-07-05")
    text = contents[-1]["parts"][0]["text"]
    assert "[Reminder:" in text
    assert "tool result" in text
