"""
Phase 1 — Meal Blueprint & User Profile Integration Tests

Verifies that the three Phase-1 features work correctly in isolation:

  1. _build_context() injects all user_profile hard-constraints into the LLM
     system-context string (default_servings, meat_veg_ratio, include_soup,
     calorie_target, disliked_ingredients, cuisine_weights).

  2. PlanAheadPipeline.execute() accepts the new user_profile keyword argument
     without raising a TypeError (signature compatibility check).

  3. PipelineStorage.get_user_profile() calls GET /api/users/{owner_id} and
     returns a dict on success, or None on HTTP/network failure.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
from app.storage.pipeline_storage import PipelineStorage


# ─── Shared helpers ──────────────────────────────────────────────────────────

def _pipeline() -> PlanAheadPipeline:
    return PlanAheadPipeline(gemini_api_url="http://fake")


def _empty_state() -> dict:
    return {"meal_plan_slots": {}, "dish_ingredients": {}, "meal_plan": {}}


def _full_profile(**overrides) -> dict:
    """Return a realistic user_profile dict for use in tests."""
    base = {
        "default_servings": 2,
        "meat_veg_ratio": "2:1:1",
        "include_soup": True,
        "calorie_target": 700,
        "disliked_ingredients": ["香菜", "花椒"],
        "cuisine_weights": {
            "Chinese": 60, "Western": 20, "Japanese": 15, "Korean": 5,
        },
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# 1. _build_context() — user_profile injection
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildContextUserProfile:
    """_build_context() must inject all user_profile fields as hard constraints."""

    # ── 1a. Section header present when profile is provided ─────────────────

    def test_blueprint_section_present_when_profile_provided(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile()
        )
        assert "USER MEAL BLUEPRINT" in ctx

    def test_no_blueprint_section_when_profile_is_none(self):
        ctx = _pipeline()._build_context(_empty_state(), None, user_profile=None)
        assert "USER MEAL BLUEPRINT" not in ctx

    # ── 1b. default_servings ─────────────────────────────────────────────────

    def test_default_servings_injected(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(default_servings=3)
        )
        assert "3 person" in ctx

    def test_default_servings_singular_for_one(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(default_servings=1)
        )
        assert "1 person" in ctx

    # ── 1c. meat_veg_ratio ───────────────────────────────────────────────────

    def test_meat_veg_ratio_injected(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(meat_veg_ratio="2:1:1")
        )
        assert "2 meat" in ctx
        assert "1 vegetable" in ctx
        assert "1 staple" in ctx

    def test_meat_veg_ratio_all_zeros_handled(self):
        """0:0:0 ratio must not crash the pipeline (edge case)."""
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(meat_veg_ratio="0:0:0")
        )
        assert "USER MEAL BLUEPRINT" in ctx

    def test_malformed_ratio_does_not_crash(self):
        """A non-standard ratio string must not raise an exception."""
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(meat_veg_ratio="invalid")
        )
        assert "USER MEAL BLUEPRINT" in ctx

    # ── 1d. include_soup ─────────────────────────────────────────────────────

    def test_include_soup_true_generates_soup_rule(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(include_soup=True)
        )
        assert "soup" in ctx.lower()

    def test_include_soup_false_omits_soup_rule(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(include_soup=False)
        )
        blueprint_start = ctx.find("USER MEAL BLUEPRINT")
        live_cal_start = ctx.find("LIVE CALENDAR", blueprint_start)
        blueprint_section = ctx[blueprint_start:live_cal_start] if live_cal_start != -1 else ctx[blueprint_start:]
        # "MUST include at least one soup" should NOT appear when include_soup=False
        assert "MUST include at least one soup" not in blueprint_section

    # ── 1e. calorie_target ───────────────────────────────────────────────────

    def test_calorie_target_injected_when_set(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(calorie_target=650)
        )
        assert "650" in ctx and "kcal" in ctx

    def test_calorie_target_omitted_when_none(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile(calorie_target=None)
        )
        assert "kcal" not in ctx

    # ── 1f. disliked_ingredients ─────────────────────────────────────────────

    def test_disliked_ingredients_injected(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            user_profile=_full_profile(disliked_ingredients=["香菜", "花椒"])
        )
        assert "香菜" in ctx
        assert "花椒" in ctx

    def test_forbidden_keyword_present_for_dislikes(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            user_profile=_full_profile(disliked_ingredients=["香菜"])
        )
        assert "FORBIDDEN" in ctx

    def test_empty_disliked_list_omits_forbidden_section(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            user_profile=_full_profile(disliked_ingredients=[])
        )
        assert "FORBIDDEN" not in ctx

    def test_none_disliked_list_omits_forbidden_section(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            user_profile=_full_profile(disliked_ingredients=None)
        )
        assert "FORBIDDEN" not in ctx

    # ── 1g. cuisine_weights — removed from prompt (AI no longer receives weights) ──

    def test_cuisine_weights_not_injected_into_context(self):
        """Cuisine weights are stored in the profile but no longer exposed to the LLM."""
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            user_profile=_full_profile(
                cuisine_weights={"Chinese": 60, "Western": 20, "Japanese": 15, "Korean": 5}
            ),
        )
        assert "Cuisine preference weights" not in ctx
        assert "60%" not in ctx

    def test_empty_cuisine_weights_no_cuisine_line(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            user_profile=_full_profile(cuisine_weights={})
        )
        assert "Cuisine preference" not in ctx

    # ── 1h. Profile does not interfere with other context sections ───────────

    def test_inventory_section_still_present_with_profile(self):
        inventory = [{"product_name": "鸡蛋", "quantity": "6", "estimated_shelf_life_days": 7}]
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            inventory_items=inventory,
            user_profile=_full_profile(),
        )
        assert "鸡蛋" in ctx
        assert "USER MEAL BLUEPRINT" in ctx

    def test_cooking_level_section_still_present_with_profile(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            cooking_level="intermediate",
            user_profile=_full_profile(),
        )
        assert "intermediate" in ctx.lower() or "Some Experience" in ctx
        assert "USER MEAL BLUEPRINT" in ctx

    def test_language_section_still_present_with_profile(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None,
            language="zh",
            user_profile=_full_profile(),
        )
        assert "LANGUAGE" in ctx.upper()
        assert "USER MEAL BLUEPRINT" in ctx

    # ── 1i. Blueprint section ordering (appears before LIVE CALENDAR) ────────

    def test_blueprint_appears_before_instructions_section(self):
        ctx = _pipeline()._build_context(
            _empty_state(), None, user_profile=_full_profile()
        )
        blueprint_pos = ctx.find("USER MEAL BLUEPRINT")
        instructions_pos = ctx.find("=== INSTRUCTIONS ===")
        assert blueprint_pos != -1
        assert instructions_pos != -1
        assert blueprint_pos < instructions_pos


# ─────────────────────────────────────────────────────────────────────────────
# 2. execute() — signature compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteSignatureAcceptsUserProfile:
    """
    PlanAheadPipeline.execute() must accept user_profile as a keyword argument
    without a TypeError (no live LLM calls — only inspects the signature).
    """

    def test_execute_has_user_profile_parameter(self):
        import inspect
        sig = inspect.signature(PlanAheadPipeline.execute)
        assert "user_profile" in sig.parameters, (
            "execute() is missing the user_profile parameter introduced in Phase 1"
        )

    def test_user_profile_defaults_to_none(self):
        import inspect
        sig = inspect.signature(PlanAheadPipeline.execute)
        param = sig.parameters["user_profile"]
        assert param.default is None, "user_profile should default to None"

    def test_build_context_has_user_profile_parameter(self):
        import inspect
        sig = inspect.signature(PlanAheadPipeline._build_context)
        assert "user_profile" in sig.parameters


# ─────────────────────────────────────────────────────────────────────────────
# 3. PipelineStorage.get_user_profile()
# ─────────────────────────────────────────────────────────────────────────────

class TestGetUserProfile:
    """
    PipelineStorage.get_user_profile() must:
    - Return the user dict from DataStorageService on success (HTTP 200).
    - Return None when base_url is not configured.
    - Return None on HTTP 404 / network errors (degrade gracefully).
    """

    @pytest.mark.asyncio
    async def test_returns_user_dict_on_success(self):
        expected = {
            "id": 7,
            "display_name": "Test User",
            "default_servings": 2,
            "cuisine_weights": {"Chinese": 60, "Western": 40},
            "disliked_ingredients": ["香菜"],
        }
        mock_response = MagicMock()
        mock_response.json.return_value = expected
        mock_response.raise_for_status = MagicMock()

        storage = PipelineStorage()
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://fake-ds"), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            result = await storage.get_user_profile(owner_id=7)

        assert result == expected
        assert result["cuisine_weights"]["Chinese"] == 60
        assert "香菜" in result["disliked_ingredients"]

    @pytest.mark.asyncio
    async def test_returns_none_when_no_base_url(self):
        storage = PipelineStorage()
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value=None):
            result = await storage.get_user_profile(owner_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        import httpx

        storage = PipelineStorage()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )

        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://fake-ds"), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            result = await storage.get_user_profile(owner_id=99)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_exception(self):
        storage = PipelineStorage()
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://fake-ds"), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("connection refused")):
            result = await storage.get_user_profile(owner_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self):
        """Verify that /api/users/{owner_id} is the URL called."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 42}
        mock_response.raise_for_status = MagicMock()

        captured_url = []

        async def _fake_get(url, **kwargs):
            captured_url.append(url)
            return mock_response

        storage = PipelineStorage()
        with patch("app.storage.pipeline_storage._get_storage_base_url", return_value="http://fake-ds"), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=_fake_get):
            await storage.get_user_profile(owner_id=42)

        assert len(captured_url) == 1
        assert "/api/users/42" in captured_url[0] or captured_url[0] == "/api/users/42"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Integration: full profile round-trip through _build_context
# ─────────────────────────────────────────────────────────────────────────────

class TestFullProfileContextRoundTrip:
    """
    Simulate the real scenario: a profile fetched from DB is passed through
    _build_context(), and all constraints appear in the final LLM prompt.
    """

    def test_typical_single_person_student_profile(self):
        """USC学生 — 1人份、不吃香菜。"""
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": ["香菜"],
            "cuisine_weights": {"Chinese": 70, "Western": 20, "Japanese": 10},
        }
        ctx = _pipeline()._build_context(_empty_state(), None, user_profile=profile)
        assert "1 person" in ctx
        assert "香菜" in ctx
        assert "FORBIDDEN" in ctx
        assert "Cuisine preference weights" not in ctx
        assert "70%" not in ctx
        assert "MUST include at least one soup" not in ctx

    def test_family_profile_with_all_constraints(self):
        """5口之家 — 5人份、必须有汤、卡路里限制 800kcal."""
        profile = {
            "default_servings": 5,
            "meat_veg_ratio": "2:2:1",
            "include_soup": True,
            "calorie_target": 800,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 50, "Western": 30, "Japanese": 20},
        }
        ctx = _pipeline()._build_context(_empty_state(), None, user_profile=profile)
        assert "5 person" in ctx
        assert "2 meat" in ctx
        assert "soup" in ctx.lower()
        assert "800" in ctx and "kcal" in ctx
        assert "FORBIDDEN" not in ctx

    def test_profile_with_no_cuisine_weights_still_works(self):
        """A profile missing cuisine_weights degrades gracefully."""
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": True,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": None,
        }
        ctx = _pipeline()._build_context(_empty_state(), None, user_profile=profile)
        assert "USER MEAL BLUEPRINT" in ctx
        assert "Cuisine preference" not in ctx
