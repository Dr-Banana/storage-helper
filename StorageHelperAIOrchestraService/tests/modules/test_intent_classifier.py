"""
Tests for IntentClassifier — covers RECIPE_QA intent, DISCUSSING_RECIPE session block,
and the PLANNING session block.  All LLM calls are mocked so tests run offline.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.modules.intent_classifier import IntentClassifier, Intent, IntentClassificationResult


def _make_gemini_response(intent: str, confidence: float = 0.95, reasoning: str = "test") -> dict:
    """Build a minimal fake Gemini API response payload."""
    body = json.dumps({"intent": intent, "confidence": confidence, "reasoning": reasoning})
    return {
        "candidates": [{"content": {"parts": [{"text": body}]}}]
    }


@pytest.fixture
def classifier():
    return IntentClassifier(model_name="gemini-test", api_key="fake-key")


# ---------------------------------------------------------------------------
# Intent enum
# ---------------------------------------------------------------------------

class TestIntentEnum:
    def test_recipe_qa_in_enum(self):
        assert Intent.RECIPE_QA == "RECIPE_QA"

    def test_all_intents_present(self):
        names = {i.value for i in Intent}
        assert names >= {"SEARCH", "UPDATE", "PLAN_AHEAD", "COOKING_STEPS", "RECIPE_QA", "GENERAL"}


# ---------------------------------------------------------------------------
# Session block injection
# ---------------------------------------------------------------------------

class TestSessionBlockInjection:
    """Verify the correct session block is injected into the LLM payload."""

    @pytest.mark.asyncio
    async def test_planning_session_block_injected(self, classifier):
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("PLAN_AHEAD")
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await classifier.classify("什么都行", session_mode="PLANNING")

        text = captured["payload"]["contents"][0]["parts"][0]["text"]
        assert "ACTIVE SESSION: MEAL PLANNING" in text
        assert "Prefer PLAN_AHEAD" in text

    @pytest.mark.asyncio
    async def test_discussing_recipe_block_injected(self, classifier):
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("RECIPE_QA")
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await classifier.classify(
                "酱料比例是多少",
                cooking_context={"dish_name": "蒜泥白肉", "steps": ["步骤1", "步骤2"]},
            )

        text = captured["payload"]["contents"][0]["parts"][0]["text"]
        assert "ACTIVE SESSION: DISCUSSING RECIPE" in text
        assert "蒜泥白肉" in text
        assert "RECIPE_QA" in text

    @pytest.mark.asyncio
    async def test_discussing_recipe_takes_priority_over_planning(self, classifier):
        """When both cooking_context and session_mode=PLANNING are set,
        DISCUSSING_RECIPE block should win (cooking_context checked first)."""
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("RECIPE_QA")
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await classifier.classify(
                "多久能煮好",
                session_mode="PLANNING",
                cooking_context={"dish_name": "白切鸡", "steps": ["s1"]},
            )

        text = captured["payload"]["contents"][0]["parts"][0]["text"]
        assert "DISCUSSING RECIPE" in text
        assert "MEAL PLANNING" not in text

    @pytest.mark.asyncio
    async def test_no_session_block_when_no_context(self, classifier):
        """No active session → no session block injected."""
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("GENERAL")
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await classifier.classify("你好")

        text = captured["payload"]["contents"][0]["parts"][0]["text"]
        assert "ACTIVE SESSION" not in text


# ---------------------------------------------------------------------------
# classify() return values
# ---------------------------------------------------------------------------

class TestClassifyReturnValues:

    @pytest.mark.asyncio
    async def test_returns_recipe_qa_intent(self, classifier):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("RECIPE_QA", confidence=0.92)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await classifier.classify(
                "标准比例是多少",
                cooking_context={"dish_name": "蒜泥白肉", "steps": ["s1"]},
            )

        assert result.intent == Intent.RECIPE_QA
        assert result.confidence == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_fallback_to_general_on_api_error(self, classifier):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("network error"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await classifier.classify("hello")

        assert result.intent == Intent.GENERAL
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_steps_preview_in_discussing_recipe_block(self, classifier):
        """Steps should be truncated to 3 in the injected block."""
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("RECIPE_QA")
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await classifier.classify(
                "第四步怎么做",
                cooking_context={
                    "dish_name": "红烧肉",
                    "steps": ["s1", "s2", "s3", "s4", "s5"],
                },
            )

        text = captured["payload"]["contents"][0]["parts"][0]["text"]
        # Steps preview should be at most first 3 + "..."
        assert "s1" in text
        assert "..." in text

    @pytest.mark.asyncio
    async def test_history_included_in_payload(self, classifier):
        """Last 5 history messages should appear in the request."""
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("GENERAL")
            return mock_resp

        history = [
            {"role": "user", "content": "今天晚上什么计划"},
            {"role": "assistant", "content": "您今晚是蒜泥白肉"},
        ]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await classifier.classify("酱料比例", history=history)

        text = captured["payload"]["contents"][0]["parts"][0]["text"]
        assert "今天晚上什么计划" in text
        assert "蒜泥白肉" in text
