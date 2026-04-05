"""
Tests for PlanCookHomeAgent.

All external I/O (HTTP calls to DataStorageService) is mocked so the suite
runs fully offline.
"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.plan_cook_home_agent import PlanCookHomeAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(json_body: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
        resp.text = "error"
    return resp


def _doc_receipt(items: list) -> dict:
    """Build a document dict that looks like a receipt."""
    return {"metadata": {"items": items}}


def _doc_item(product_name: str, is_food: bool, category: str = "produce") -> dict:
    """Build a document dict that is a single item."""
    return {
        "title": product_name,
        "metadata": {
            "product_name": product_name,
            "is_food": is_food,
            "category": category,
            "quantity": "2",
            "storage_suggestion": "fridge",
            "estimated_shelf_life_days": 7,
        },
    }


@pytest.fixture
def agent():
    return PlanCookHomeAgent()


# ---------------------------------------------------------------------------
# _get_user_inventory
# ---------------------------------------------------------------------------

class TestGetUserInventory:

    @patch("app.agents.plan_cook_home_agent._get_storage_base_url", return_value="http://storage")
    @pytest.mark.asyncio
    async def test_returns_food_items_from_receipt(self, mock_url, agent):
        """Should extract only is_food=True items from receipt documents."""
        docs = [
            _doc_receipt([
                {"product_name": "Chicken", "is_food": True, "category": "meat"},
                {"product_name": "Shampoo", "is_food": False, "category": "hygiene"},
            ])
        ]
        mock_resp = _make_response({"documents": docs})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await agent._get_user_inventory(owner_id=1)

        assert len(result) == 1
        assert result[0]["product_name"] == "Chicken"
        assert result[0]["category"] == "meat"

    @patch("app.agents.plan_cook_home_agent._get_storage_base_url", return_value="http://storage")
    @pytest.mark.asyncio
    async def test_returns_individual_food_item_documents(self, mock_url, agent):
        """Should include single-item documents where is_food=True."""
        docs = [_doc_item("Tomato", is_food=True), _doc_item("Detergent", is_food=False)]
        mock_resp = _make_response({"documents": docs})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await agent._get_user_inventory(owner_id=1)

        assert len(result) == 1
        assert result[0]["product_name"] == "Tomato"

    @patch("app.agents.plan_cook_home_agent._get_storage_base_url", return_value="http://storage")
    @pytest.mark.asyncio
    async def test_returns_empty_list_on_no_documents(self, mock_url, agent):
        """Should return [] when the user has no documents."""
        mock_resp = _make_response({"documents": []})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await agent._get_user_inventory(owner_id=1)

        assert result == []

    @patch("app.agents.plan_cook_home_agent._get_storage_base_url", return_value=None)
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_base_url_missing(self, mock_url, agent):
        """Should return [] gracefully when STORAGE_SERVICE_URL is not configured."""
        result = await agent._get_user_inventory(owner_id=1)
        assert result == []

    @patch("app.agents.plan_cook_home_agent._get_storage_base_url", return_value="http://storage")
    @pytest.mark.asyncio
    async def test_returns_empty_list_on_read_timeout(self, mock_url, agent):
        """Should return [] and log a warning (not raise) on ReadTimeout."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
            mock_client_cls.return_value = mock_client

            result = await agent._get_user_inventory(owner_id=1)

        assert result == []

    @patch("app.agents.plan_cook_home_agent._get_storage_base_url", return_value="http://storage")
    @pytest.mark.asyncio
    async def test_returns_empty_list_on_connect_error(self, mock_url, agent):
        """Should return [] when the storage service is unreachable."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_client_cls.return_value = mock_client

            result = await agent._get_user_inventory(owner_id=1)

        assert result == []

    @patch("app.agents.plan_cook_home_agent._get_storage_base_url", return_value="http://storage")
    @pytest.mark.asyncio
    async def test_returns_empty_list_on_http_error(self, mock_url, agent):
        """Should return [] on a 4xx/5xx HTTP response."""
        mock_resp = _make_response({}, status_code=500)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await agent._get_user_inventory(owner_id=1)

        assert result == []


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

class TestExecute:

    @pytest.mark.asyncio
    async def test_execute_with_inventory_returns_plan_cook_home_action(self, agent):
        """execute() should return action=PLAN_COOK_HOME with inventory data."""
        inventory = [
            {"product_name": "Egg", "category": "dairy", "quantity": "6",
             "storage_suggestion": "fridge", "estimated_shelf_life_days": 14},
            {"product_name": "Spinach", "category": "vegetable", "quantity": "1",
             "storage_suggestion": "fridge", "estimated_shelf_life_days": 5},
        ]
        with patch.object(agent, "_get_user_inventory", AsyncMock(return_value=inventory)):
            result = await agent.execute(user_input="今晚吃什么", owner_id=2)

        assert result["action"] == "PLAN_COOK_HOME"
        assert result["data"]["inventory_count"] == 2
        assert result["data"]["inventory_items"] == inventory
        assert "Egg" in result["data"]["suggestion"]

    @pytest.mark.asyncio
    async def test_execute_without_inventory_suggests_upload(self, agent):
        """execute() should suggest uploading a receipt when inventory is empty."""
        with patch.object(agent, "_get_user_inventory", AsyncMock(return_value=[])):
            result = await agent.execute(user_input="今晚吃什么", owner_id=2)

        assert result["action"] == "PLAN_COOK_HOME"
        assert result["data"]["inventory_count"] == 0
        assert result["data"]["inventory_items"] == []
        assert "receipt" in result["data"]["suggestion"].lower() or "upload" in result["data"]["suggestion"].lower()

    @pytest.mark.asyncio
    async def test_execute_caps_inventory_at_20_items_in_suggestion(self, agent):
        """Suggestion text should include at most 20 items even if inventory is larger."""
        inventory = [
            {"product_name": f"Item{i}", "category": "food", "quantity": "1",
             "storage_suggestion": "", "estimated_shelf_life_days": 3}
            for i in range(25)
        ]
        with patch.object(agent, "_get_user_inventory", AsyncMock(return_value=inventory)):
            result = await agent.execute(user_input="帮我规划", owner_id=3)

        # inventory_items returns all 25; suggestion text is capped at 20
        assert result["data"]["inventory_count"] == 25
        suggestion_lines = [
            line for line in result["data"]["suggestion"].splitlines()
            if line.startswith("- ")
        ]
        assert len(suggestion_lines) == 20
