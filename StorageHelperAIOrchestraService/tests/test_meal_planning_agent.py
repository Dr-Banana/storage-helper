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
        language=kwargs.get("language", "zh-CN"),
        user_timezone=kwargs.get("user_timezone", "Asia/Shanghai"),
    )


# ── Agent loop — basic behavior ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_only_response_returned_immediately():
    """No tool calls → return the text directly without extra rounds."""
    agent = make_agent()
    with patch.object(agent, "_call_gemini", new=AsyncMock(return_value=_gemini_text("今晚吃面条"))):
        reply = await agent.run("今晚吃什么", [], user_timezone="Asia/Shanghai")
    assert reply == "今晚吃面条"


@pytest.mark.asyncio
async def test_single_tool_call_then_text():
    """fetch call → result injected → Gemini returns final text."""
    agent = make_agent()
    responses = iter([
        _gemini_tool_call("fetch_meal_plan", {"date": "2026-06-14", "meal_type": "dinner"}),
        _gemini_text("今晚有西葫芦鸡蛋汤"),
    ])
    with patch.object(agent, "_call_gemini", new=AsyncMock(side_effect=lambda *a, **kw: next(responses))), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)):
        reply = await agent.run("今晚吃什么", [], user_timezone="Asia/Shanghai")
    assert reply == "今晚有西葫芦鸡蛋汤"


@pytest.mark.asyncio
async def test_fetch_save_text_three_round_flow():
    """Two tool calls (fetch → save) then final text — models the two-phase save."""
    agent = make_agent()
    responses = iter([
        _gemini_tool_call("fetch_meal_plan", {"date": "2026-06-14", "meal_type": "dinner"}),
        _gemini_tool_call("save_meal_plan", {
            "date": "2026-06-14", "meal_type": "dinner",
            "dishes": [{"name": "面条", "ingredients": [], "steps": ["煮"]}],
        }),
        _gemini_text("已保存今晚面条！"),
    ])
    with patch.object(agent, "_call_gemini", new=AsyncMock(side_effect=lambda *a, **kw: next(responses))), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.save_plan",
               new=AsyncMock(return_value={"id": 99})):
        reply = await agent.run("帮我计划今晚", [], user_timezone="Asia/Shanghai")
    assert reply == "已保存今晚面条！"


@pytest.mark.asyncio
async def test_max_rounds_returns_fallback_chinese():
    """After 10 rounds of tool calls, return a Chinese fallback (language=zh-CN)."""
    agent = make_agent(language="zh-CN")
    with patch.object(agent, "_call_gemini",
                      new=AsyncMock(return_value=_gemini_tool_call("fetch_meal_plan", {"date": "2026-06-14"}))), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=None)), \
         patch("app.agents.meal_planning_agent.schedule_commands.fetch_upcoming",
               new=AsyncMock(return_value=[])):
        reply = await agent.run("今晚吃什么", [], user_timezone="Asia/Shanghai")
    assert "重试" in reply


@pytest.mark.asyncio
async def test_empty_gemini_response_returns_non_empty_fallback():
    """
    Regression: empty parts used to return '' silently.
    Now returns a user-visible error message.
    """
    agent = make_agent(language="zh-CN")
    with patch.object(agent, "_call_gemini", new=AsyncMock(return_value=_gemini_empty())):
        reply = await agent.run("今晚吃什么", [], user_timezone="Asia/Shanghai")
    assert reply, "Empty Gemini response must produce a non-empty fallback, not silent ''"
    assert "重试" in reply


@pytest.mark.asyncio
async def test_on_text_callback_called_with_final_text():
    """on_text streaming callback fires with the final reply."""
    agent = make_agent()
    collected = []
    with patch.object(agent, "_call_gemini", new=AsyncMock(return_value=_gemini_text("回复内容"))):
        await agent.run("问题", [], on_text=collected.append)
    assert collected == ["回复内容"]


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
        {"name": "西葫芦鸡蛋汤", "ingredients": [{"name": "西葫芦", "quantity": "1个"}], "steps": ["step1"]}
    ])
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_existing",
               new=AsyncMock(return_value=record)):
        result = await agent._tool_fetch({"date": "2026-06-14", "meal_type": "dinner"})
    assert result["found"] is True
    assert result["dishes"][0]["name"] == "西葫芦鸡蛋汤"


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
        {"date": "2026-06-14", "meal_type": "dinner", "dishes": ["西葫芦鸡蛋汤"]},
    ]
    with patch("app.agents.meal_planning_agent.schedule_commands.fetch_upcoming",
               new=AsyncMock(return_value=summaries)):
        result = await agent._tool_fetch({"date": "2026-06-14"})
    assert result["found"] is True
    all_dish_names = [d for m in result["meals"] for d in m["dishes"]]
    assert "西葫芦鸡蛋汤" in all_dish_names
    assert "Curry Beef" not in all_dish_names


# ── _tool_save: create vs update ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_creates_new_when_no_existing():
    agent = make_agent()
    dishes = [{"name": "面条", "ingredients": [{"name": "面", "quantity": "100g"}], "steps": ["煮"]}]
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
        {"name": "西葫芦鸡蛋汤", "ingredients": [], "steps": ["step"]}
    ])
    dishes = [
        {"name": "西葫芦鸡蛋汤"},
        {"name": "菠萝咕唠肉", "ingredients": [{"name": "猪肉", "quantity": "200g"}], "steps": ["炸", "调汁"]},
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
    dishes = [{"name": "面条", "ingredients": [], "steps": ["煮"]}]
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
    stored = [{"name": "西葫芦鸡蛋汤", "ingredients": [{"name": "西葫芦", "quantity": "1个"}], "steps": ["step1", "step2"]}]
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
                {"name": "西葫芦鸡蛋汤"},  # ← name only, kept dish
                {"name": "菠萝咕唠肉", "ingredients": [{"name": "猪肉", "quantity": "200g"}], "steps": ["炸"]},
            ],
        })

    zucchini = next(d for d in captured["dishes"] if d["name"] == "西葫芦鸡蛋汤")
    assert zucchini["steps"] == ["step1", "step2"], "Kept dish must be enriched from DB"
    assert zucchini["ingredients"][0]["name"] == "西葫芦"

    pineapple = next(d for d in captured["dishes"] if d["name"] == "菠萝咕唠肉")
    assert pineapple["steps"] == ["炸"], "New dish must keep model-provided data"


@pytest.mark.asyncio
async def test_dish_with_steps_from_model_not_overwritten_by_db():
    """
    When the model provides full steps for a dish that already exists in DB,
    the model's data wins (modify/replace scenario).
    """
    agent = make_agent()
    existing = _sample_record("2026-06-14", "dinner", [
        {"name": "西红柿炒蛋", "ingredients": [], "steps": ["old step"]}
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
            "dishes": [{"name": "西红柿炒蛋", "ingredients": [], "steps": ["new step"]}],
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
            "dishes": [{"name": "新菜"}],
        })

    assert captured["dishes"][0]["name"] == "新菜"
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
    contents = agent._build_contents([], "今晚吃什么", "2026-06-14")
    last = contents[-1]
    assert last["role"] == "user"
    text = last["parts"][0]["text"]
    assert "[Today: 2026-06-14]" in text
    assert "[Level: intermediate]" in text
    assert "今晚吃什么" in text


def test_build_contents_maps_assistant_role_to_model():
    """Gemini requires 'model', not 'assistant', for AI turns."""
    agent = make_agent()
    history = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    contents = agent._build_contents(history, "下一条", "2026-06-14")
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
