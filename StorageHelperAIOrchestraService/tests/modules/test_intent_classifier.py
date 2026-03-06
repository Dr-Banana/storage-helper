"""
Tests for IntentClassifier — covers RECIPE_QA intent, DISCUSSING_RECIPE session block,
and the PLANNING session block.  All LLM calls are mocked so tests run offline.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.modules.intent_classifier import IntentClassifier, Intent, IntentClassificationResult


def _make_gemini_response(
    intent: str,
    confidence: float = 0.95,
    reasoning: str = "test",
    compound_intents=None,
    extracted_items=None,
) -> dict:
    """Build a minimal fake Gemini API response payload."""
    data: dict = {"intent": intent, "confidence": confidence, "reasoning": reasoning}
    if compound_intents is not None:
        data["compound_intents"] = compound_intents
    if extracted_items is not None:
        data["extracted_items"] = extracted_items
    body = json.dumps(data)
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


# ---------------------------------------------------------------------------
# Compound intent
# ---------------------------------------------------------------------------

class TestCompoundIntent:
    """Tests for the COMPOUND_INTENTS + EXTRACTED_ITEMS feature."""

    @pytest.mark.asyncio
    async def test_compound_plan_ahead_and_cooking_steps(self, classifier):
        """LLM returns compound_intents with PLAN_AHEAD + COOKING_STEPS."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_gemini_response(
            "PLAN_AHEAD",
            confidence=0.97,
            compound_intents=["PLAN_AHEAD", "COOKING_STEPS"],
            extracted_items=["宫保鸡丁", "鱼香肉丝"],
        )

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await classifier.classify("今天晚上加宫保鸡丁和鱼香肉丝，附上做法")

        assert result.intent == Intent.PLAN_AHEAD
        assert Intent.PLAN_AHEAD in (result.compound_intents or [])
        assert Intent.COOKING_STEPS in (result.compound_intents or [])
        assert result.extracted_items == ["宫保鸡丁", "鱼香肉丝"]

    @pytest.mark.asyncio
    async def test_single_intent_has_no_compound(self, classifier):
        """A plain single-task message should return no compound_intents."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_gemini_response("PLAN_AHEAD", compound_intents=None)

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await classifier.classify("帮我计划一下本周的饮食")

        assert result.compound_intents is None

    @pytest.mark.asyncio
    async def test_compound_prompt_mentions_compound_rule(self, classifier):
        """Verify the COMPOUND INTENT RULE is present in the system prompt payload."""
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_gemini_response("PLAN_AHEAD")
            return mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            await classifier.classify("加两道菜并附上步骤")

        sys_text = captured["payload"]["systemInstruction"]["parts"][0]["text"]
        assert "COMPOUND INTENT RULE" in sys_text
        assert "compound_intents" in sys_text
        assert "extracted_items" in sys_text

    def test_intent_classification_result_has_compound_fields(self):
        """IntentClassificationResult accepts optional compound fields."""
        r = IntentClassificationResult(
            intent=Intent.PLAN_AHEAD,
            confidence=0.9,
            reasoning="test",
            compound_intents=[Intent.PLAN_AHEAD, Intent.COOKING_STEPS],
            extracted_items=["番茄炒蛋"],
        )
        assert r.compound_intents == [Intent.PLAN_AHEAD, Intent.COOKING_STEPS]
        assert r.extracted_items == ["番茄炒蛋"]

    def test_intent_classification_result_compound_optional(self):
        """compound_intents and extracted_items default to None."""
        r = IntentClassificationResult(
            intent=Intent.GENERAL,
            confidence=0.8,
            reasoning="hello",
        )
        assert r.compound_intents is None
        assert r.extracted_items is None
