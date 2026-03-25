import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.storage.pipeline_storage import LocationDataHandler, PipelineStorage


# ---------------------------------------------------------------------------
# Helpers shared by multiple test classes
# ---------------------------------------------------------------------------

def _make_schedule_for_recent(date_str: str, meal_time: str, dish_names: list) -> dict:
    """Build a minimal schedule response dict for get_recent_dishes_from_schedules tests."""
    return {
        "metadata": {
            "features": [
                {
                    "type": "meal_plan",
                    "plans": [
                        {
                            "date": date_str,
                            "meals": [
                                {
                                    "mealTime": meal_time,
                                    "dishes": [{"name": n} for n in dish_names],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }


def _make_schedule(*dishes):
    """Helper: build a minimal schedule dict containing the given dish dicts."""
    return {
        "metadata": {
            "features": [
                {
                    "type": "meal_plan",
                    "plans": [
                        {
                            "date": "2026-03-10",
                            "meals": [
                                {
                                    "mealTime": "dinner",
                                    "dishes": list(dishes),
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }

def test_location_data_handler_db_to_llm():
    db_locations = {
        1: ["Kitchen", "Shelf for food storage"],
        2: ["Office", "Drawer for tax papers and receipts"]
    }
    llm_format = LocationDataHandler.format_db_locations_for_llm(db_locations)
    
    assert llm_format[1]["name"] == "Kitchen"
    assert llm_format[1]["description"] == "Shelf for food storage"
    assert llm_format[2]["name"] == "Office"
    assert "tax papers" in llm_format[2]["description"]

def test_location_data_handler_llm_to_db():
    llm_locations = {
        1: {"name": "Kitchen", "description": "Shelf"}
    }
    db_format = LocationDataHandler.format_llm_locations_for_db(llm_locations)
    
    assert db_format[1] == ["Kitchen", "Shelf"]

@pytest.mark.asyncio
async def test_pipeline_storage_upload_file_only():
    mock_client = MagicMock()
    storage = PipelineStorage(storage_client=mock_client)
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"image_url": "http://storage/file.jpg"}
    mock_response.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post, \
         patch.object(PipelineStorage, '_read_file_content', new_callable=AsyncMock) as mock_read:
        
        mock_post.return_value = mock_response
        mock_read.return_value = b"fake content"
        
        result = await storage.upload_file_only("test.jpg", 1)
        assert result == "http://storage/file.jpg"

@pytest.mark.asyncio
async def test_pipeline_storage_process_document_page():
    mock_client = MagicMock()
    storage = PipelineStorage(storage_client=mock_client)
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "document_id": 123,
        "page_id": 456,
        "status": "success"
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await storage.process_document_page(
            image_url="http://storage/file.jpg",
            owner_id=1,
            page_number=1,
            ocr_text="some text"
        )
        
        assert result["document_id"] == 123
        assert result["page_id"] == 456
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# _extract_existing_dish_data
# ---------------------------------------------------------------------------

class TestExtractExistingDishData:
    """Unit tests for PipelineStorage._extract_existing_dish_data (static method)."""

    def test_extracts_dish_with_steps(self):
        schedule = _make_schedule(
            {"name": "宫保鸡丁", "cookingSteps": ["步骤1", "步骤2"], "ingredients": []}
        )
        result = PipelineStorage._extract_existing_dish_data(schedule)
        assert "宫保鸡丁" in result
        assert result["宫保鸡丁"]["steps"] == ["步骤1", "步骤2"]

    def test_extracts_dish_with_ingredient_quantities(self):
        schedule = _make_schedule(
            {
                "name": "番茄炒蛋",
                "cookingSteps": [],
                "ingredients": [
                    {"name": "番茄", "quantity": "200g"},
                    {"name": "鸡蛋", "quantity": "2个"},
                ],
            }
        )
        result = PipelineStorage._extract_existing_dish_data(schedule)
        assert "番茄炒蛋" in result
        assert result["番茄炒蛋"]["steps"] == []
        assert len(result["番茄炒蛋"]["ingredients"]) == 2

    def test_skips_dish_without_steps_or_quantities(self):
        schedule = _make_schedule(
            {"name": "白饭", "cookingSteps": [], "ingredients": [{"name": "米", "quantity": ""}]}
        )
        result = PipelineStorage._extract_existing_dish_data(schedule)
        assert "白饭" not in result

    def test_skips_dish_with_no_name(self):
        schedule = _make_schedule(
            {"cookingSteps": ["step1"], "ingredients": []}
        )
        result = PipelineStorage._extract_existing_dish_data(schedule)
        assert result == {}

    def test_empty_schedule(self):
        assert PipelineStorage._extract_existing_dish_data({}) == {}

    def test_multiple_dishes_across_meals(self):
        schedule = {
            "metadata": {
                "features": [
                    {
                        "type": "meal_plan",
                        "plans": [
                            {
                                "date": "2026-03-10",
                                "meals": [
                                    {
                                        "mealTime": "lunch",
                                        "dishes": [
                                            {"name": "红烧肉", "cookingSteps": ["s1"], "ingredients": []},
                                        ],
                                    },
                                    {
                                        "mealTime": "dinner",
                                        "dishes": [
                                            {"name": "清蒸鱼", "cookingSteps": ["s1", "s2"], "ingredients": []},
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        }
        result = PipelineStorage._extract_existing_dish_data(schedule)
        assert "红烧肉" in result
        assert "清蒸鱼" in result

    def test_backward_compat_wrapper_returns_steps_only(self):
        schedule = _make_schedule(
            {"name": "鱼香肉丝", "cookingSteps": ["step1", "step2"], "ingredients": []}
        )
        steps = PipelineStorage._extract_cooking_steps_from_schedule(schedule)
        assert steps["鱼香肉丝"] == ["step1", "step2"]

    def test_backward_compat_excludes_dishes_without_steps(self):
        """_extract_cooking_steps_from_schedule should omit dishes with only ingredient data."""
        schedule = _make_schedule(
            {
                "name": "蒸蛋",
                "cookingSteps": [],
                "ingredients": [{"name": "鸡蛋", "quantity": "2个"}],
            }
        )
        steps = PipelineStorage._extract_cooking_steps_from_schedule(schedule)
        assert "蒸蛋" not in steps


# ---------------------------------------------------------------------------
# _convert_to_feature_format: existing_dish_data fuzzy matching
# ---------------------------------------------------------------------------

class TestConvertToFeatureFormatDishDataPreservation:
    """Tests that _convert_to_feature_format preserves steps/quantities via existing_dish_data."""

    def _run(self, meal_plan, meal_plan_slots, dish_ingredients, existing_dish_data=None, existing_cooking_steps=None):
        storage = PipelineStorage()
        return storage._convert_to_feature_format(
            meal_plan=meal_plan,
            shopping_list=[],
            dish_ingredients=dish_ingredients,
            meal_plan_slots=meal_plan_slots,
            existing_dish_data=existing_dish_data,
            existing_cooking_steps=existing_cooking_steps,
        )

    def _find_dish(self, feature_data, dish_name):
        for feat in (feature_data.get("features") or []):
            if feat.get("type") != "meal_plan":
                continue
            for plan in feat.get("plans", []):
                for meal in plan.get("meals", []):
                    for dish in meal.get("dishes", []):
                        if dish.get("name") == dish_name:
                            return dish
        return None

    def test_exact_match_preserves_steps(self):
        meal_plan_slots = {"2026-03-10": {"dinner": ["宫保鸡丁"]}}
        existing_dish_data = {
            "宫保鸡丁": {"steps": ["步骤1", "步骤2"], "ingredients": []}
        }
        result = self._run(
            meal_plan={"2026-03-10": "宫保鸡丁"},
            meal_plan_slots=meal_plan_slots,
            dish_ingredients={"宫保鸡丁": ["鸡肉", "花生"]},
            existing_dish_data=existing_dish_data,
        )
        dish = self._find_dish(result, "宫保鸡丁")
        assert dish is not None
        assert dish.get("cookingSteps") == ["步骤1", "步骤2"]

    def test_ingredient_quantities_restored(self):
        meal_plan_slots = {"2026-03-10": {"dinner": ["番茄炒蛋"]}}
        existing_dish_data = {
            "番茄炒蛋": {
                "steps": [],
                "ingredients": [
                    {"name": "番茄", "quantity": "200g", "category": "vegetable"},
                    {"name": "鸡蛋", "quantity": "2个", "category": "protein"},
                ],
            }
        }
        result = self._run(
            meal_plan={"2026-03-10": "番茄炒蛋"},
            meal_plan_slots=meal_plan_slots,
            dish_ingredients={"番茄炒蛋": ["番茄", "鸡蛋"]},
            existing_dish_data=existing_dish_data,
        )
        dish = self._find_dish(result, "番茄炒蛋")
        assert dish is not None
        qty_map = {i["name"]: i.get("quantity") for i in dish.get("ingredients", [])}
        assert qty_map.get("番茄") == "200g"
        assert qty_map.get("鸡蛋") == "2个"

    def test_fuzzy_match_restores_quantities_not_steps(self):
        """Fuzzy-matched dish (similar but not identical name) should restore ingredient
        quantities but NOT cookingSteps (steps are only restored on exact match)."""
        meal_plan_slots = {"2026-03-10": {"dinner": ["炸鸡芝士薯条"]}}
        existing_dish_data = {
            "炸鸡薯条": {
                "steps": ["s1", "s2"],
                "ingredients": [{"name": "鸡肉", "quantity": "300g", "category": "protein"}],
            }
        }
        result = self._run(
            meal_plan={"2026-03-10": "炸鸡芝士薯条"},
            meal_plan_slots=meal_plan_slots,
            dish_ingredients={"炸鸡芝士薯条": ["鸡肉", "薯条", "芝士"]},
            existing_dish_data=existing_dish_data,
        )
        dish = self._find_dish(result, "炸鸡芝士薯条")
        assert dish is not None
        # Steps must NOT be copied (fuzzy match only)
        assert not dish.get("cookingSteps")
        # Quantities CAN be restored via fuzzy match
        qty_map = {i["name"]: i.get("quantity") for i in dish.get("ingredients", [])}
        assert qty_map.get("鸡肉") == "300g"

    def test_no_existing_data_leaves_steps_empty(self):
        meal_plan_slots = {"2026-03-10": {"dinner": ["清蒸鱼"]}}
        result = self._run(
            meal_plan={"2026-03-10": "清蒸鱼"},
            meal_plan_slots=meal_plan_slots,
            dish_ingredients={"清蒸鱼": ["鱼", "姜"]},
        )
        dish = self._find_dish(result, "清蒸鱼")
        assert dish is not None
        assert not dish.get("cookingSteps")

    def test_legacy_existing_cooking_steps_no_longer_restores_steps(self):
        """existing_cooking_steps (legacy API) no longer restores cookingSteps on its own.
        Steps are only restored when existing_dish_data is provided (new API).
        This is intentional: fuzzy-matched steps belong to a different dish and can
        become stale quickly after the background auto-gen runs.
        """
        meal_plan_slots = {"2026-03-10": {"dinner": ["红烧肉"]}}
        result = self._run(
            meal_plan={"2026-03-10": "红烧肉"},
            meal_plan_slots=meal_plan_slots,
            dish_ingredients={"红烧肉": ["猪肉"]},
            existing_cooking_steps={"红烧肉": ["step1", "step2"]},
        )
        dish = self._find_dish(result, "红烧肉")
        assert dish is not None
        # Steps are NOT restored via the legacy path alone
        assert not dish.get("cookingSteps")


# ---------------------------------------------------------------------------
# get_recent_dishes_from_schedules
# ---------------------------------------------------------------------------

def _mock_http_response(schedules: list):
    """Return an AsyncMock that simulates a successful httpx GET returning *schedules*."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = schedules
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return mock_client


class TestGetRecentDishesFromSchedules:
    """Unit tests for PipelineStorage.get_recent_dishes_from_schedules."""

    @pytest.mark.asyncio
    async def test_returns_dishes_from_single_schedule(self):
        sched = _make_schedule_for_recent("2026-03-20", "dinner", ["宫保鸡丁", "白灼菜心"])
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response([sched])):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        names = [e["dish"] for e in result]
        assert "宫保鸡丁" in names
        assert "白灼菜心" in names

    @pytest.mark.asyncio
    async def test_result_sorted_newest_first(self):
        schedules = [
            _make_schedule_for_recent("2026-03-15", "lunch",  ["麻婆豆腐"]),
            _make_schedule_for_recent("2026-03-20", "dinner", ["红烧肉"]),
            _make_schedule_for_recent("2026-03-10", "breakfast", ["白粥"]),
        ]
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response(schedules)):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        dates = [e["date"] for e in result]
        assert dates == sorted(dates, reverse=True), "entries should be sorted newest-first"

    @pytest.mark.asyncio
    async def test_deduplicates_same_dish_same_date(self):
        # Two separate schedule records with the same dish on the same date
        sched1 = _make_schedule_for_recent("2026-03-20", "dinner", ["宫保鸡丁"])
        sched2 = _make_schedule_for_recent("2026-03-20", "dinner", ["宫保鸡丁"])
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response([sched1, sched2])):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        matches = [e for e in result if e["dish"] == "宫保鸡丁" and e["date"] == "2026-03-20"]
        assert len(matches) == 1, "same (dish, date) pair should appear only once"

    @pytest.mark.asyncio
    async def test_same_dish_different_dates_kept_separately(self):
        sched1 = _make_schedule_for_recent("2026-03-18", "dinner", ["麻婆豆腐"])
        sched2 = _make_schedule_for_recent("2026-03-22", "dinner", ["麻婆豆腐"])
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response([sched1, sched2])):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        mapo_entries = [e for e in result if e["dish"] == "麻婆豆腐"]
        assert len(mapo_entries) == 2

    @pytest.mark.asyncio
    async def test_ignores_non_meal_plan_features(self):
        sched = {
            "metadata": {
                "features": [
                    {"type": "shopping_list", "items": ["葱", "姜"]},
                    {
                        "type": "meal_plan",
                        "plans": [{"date": "2026-03-20", "meals": [{"mealTime": "lunch", "dishes": [{"name": "炒饭"}]}]}],
                    },
                ]
            }
        }
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response([sched])):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        assert result == [{"dish": "炒饭", "date": "2026-03-20"}]

    @pytest.mark.asyncio
    async def test_skips_dishes_with_empty_name(self):
        sched = _make_schedule_for_recent("2026-03-20", "dinner", ["", "  ", "红烧肉"])
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response([sched])):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        assert len(result) == 1
        assert result[0]["dish"] == "红烧肉"

    @pytest.mark.asyncio
    async def test_skips_plans_without_date(self):
        sched = {
            "metadata": {
                "features": [
                    {
                        "type": "meal_plan",
                        "plans": [
                            {"date": "", "meals": [{"mealTime": "lunch", "dishes": [{"name": "炒饭"}]}]},
                            {"date": "2026-03-20", "meals": [{"mealTime": "dinner", "dishes": [{"name": "红烧肉"}]}]},
                        ],
                    }
                ]
            }
        }
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response([sched])):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        names = [e["dish"] for e in result]
        assert "炒饭" not in names
        assert "红烧肉" in names

    @pytest.mark.asyncio
    async def test_empty_schedule_list_returns_empty(self):
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=_mock_http_response([])):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        assert result == []

    @pytest.mark.asyncio
    async def test_no_storage_url_returns_empty(self):
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value=None):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        assert result == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_not_raise(self):
        import httpx
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))

        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1)

        assert result == []

    @pytest.mark.asyncio
    async def test_correct_date_range_params_sent(self):
        """Verify that start_time and end_time query params span the requested days window."""
        from datetime import datetime, timezone, timedelta

        captured = {}

        async def fake_get(url, headers=None, params=None):
            captured["params"] = params
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = []
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = fake_get

        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://storage"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            await PipelineStorage().get_recent_dishes_from_schedules(owner_id=1, days=14)

        start = datetime.fromisoformat(captured["params"]["start_time"])
        end   = datetime.fromisoformat(captured["params"]["end_time"])
        delta = end - start
        assert 13 <= delta.days <= 15, "time range should span ~14 days"

