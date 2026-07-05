"""
HowToCook MCP client — StreamableHTTP transport.

Wraps the howtocook-mcp service (github.com/worryzyy/HowToCook-mcp).

Actual MCP tool names (from the server source):
  whatToEat            — recommend today's menu given number of diners
  getRecipeById        — fetch full recipe by name (fuzzy match)
  getRecipesByCategory — list recipes in a category
  recommendMeals       — weekly meal plan with dietary restrictions
  getAllRecipes         — full dump (too large, not used here)
"""
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class HowToCookClient:
    def __init__(self, url: str):
        self._url = url
        self._session_id: Optional[str] = None
        self._initialized = False

    # ── Public API ─────────────────────────────────────────────────────────────

    async def list_tools(self) -> List[str]:
        """Return the list of tool names the MCP server advertises."""
        try:
            await self._ensure_initialized()
            result = await self._rpc("tools/list", {})
            tools = result.get("tools", [])
            names = [t.get("name") for t in tools if t.get("name")]
            logger.info("[howtocook] available tools: %s", names)
            return names
        except Exception as exc:
            logger.warning("[howtocook] tools/list failed: %s", exc)
            return []

    async def what_to_eat(self, people_count: int = 2) -> Dict[str, Any]:
        """Today's dish recommendations for *people_count* diners."""
        try:
            await self._ensure_initialized()
            result = await self._call_tool("mcp_howtocook_whatToEat", {"peopleCount": people_count})
            dishes = result.get("dishes") or []
            if dishes:
                logger.debug("[howtocook] first dish full structure: %s", json.dumps(dishes[0], ensure_ascii=False))
            return result
        except Exception as exc:
            logger.warning("[howtocook] what_to_eat failed: %s", exc)
            return {"error": str(exc)}

    async def get_recipe_details(self, query: str) -> Dict[str, Any]:
        """Fetch full recipe (ingredients + steps) by dish name (fuzzy match)."""
        try:
            await self._ensure_initialized()
            return await self._call_tool("mcp_howtocook_getRecipeById", {"query": query})
        except Exception as exc:
            logger.warning("[howtocook] get_recipe_details failed: %s", exc)
            return {"error": str(exc)}

    async def get_recipes_by_category(self, category: str) -> Dict[str, Any]:
        """List recipes in *category* (e.g. 早餐, 荤菜, 主食, 水产)."""
        try:
            await self._ensure_initialized()
            return await self._call_tool("mcp_howtocook_getRecipesByCategory", {"category": category})
        except Exception as exc:
            logger.warning("[howtocook] get_recipes_by_category failed: %s", exc)
            return {"error": str(exc)}

    async def recommend_meals(
        self,
        people_count: int,
        allergies: Optional[List[str]] = None,
        avoid_items: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate a weekly meal plan with dietary restrictions."""
        try:
            await self._ensure_initialized()
            args: Dict[str, Any] = {"peopleCount": people_count}
            if allergies:
                args["allergies"] = allergies
            if avoid_items:
                args["avoidItems"] = avoid_items
            return await self._call_tool("mcp_howtocook_recommendMeals", args)
        except Exception as exc:
            logger.warning("[howtocook] recommend_meals failed: %s", exc)
            return {"error": str(exc)}

    # ── MCP initialization ─────────────────────────────────────────────────────

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        try:
            result = await self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "storage-helper-meal-agent", "version": "1.0"},
            })
            logger.debug("[howtocook] initialize ok, session=%s result=%s",
                         self._session_id, str(result)[:200])
        except Exception as exc:
            logger.debug("[howtocook] initialize skipped (%s)", exc)
        self._initialized = True

        # Probe available tools once so we know the exact names
        await self.list_tools()

    # ── JSON-RPC transport ─────────────────────────────────────────────────────

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        logger.debug("[howtocook] --> %s  params=%s", method, json.dumps(params)[:300])

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self._url, json=body, headers=headers)

        logger.debug("[howtocook] <-- status=%d  ct=%s  body=%s",
                     resp.status_code, resp.headers.get("content-type", ""),
                     resp.text[:500])

        resp.raise_for_status()

        if sid := resp.headers.get("Mcp-Session-Id"):
            self._session_id = sid

        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct:
            data = self._parse_sse(resp.text)
        else:
            data = resp.json()

        if "error" in data:
            err = data["error"]
            code = err.get("code") if isinstance(err, dict) else ""
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"MCP error {code}: {msg}")
        return data.get("result", data)

    async def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        logger.info("[howtocook] calling tool=%s args=%s", tool_name, arguments)
        result = await self._rpc("tools/call", {"name": tool_name, "arguments": arguments})
        logger.debug("[howtocook] tool=%s raw_result=%s", tool_name, str(result)[:500])

        # MCP signals application-level errors via isError:true inside result
        if result.get("isError"):
            content = result.get("content", [])
            texts = [p["text"] for p in content if p.get("type") == "text"]
            msg = texts[0] if texts else "unknown MCP error"
            logger.error("[howtocook] tool=%s isError=true message=%s", tool_name, msg)
            raise RuntimeError(msg)

        # MCP returns content as typed parts; extract text
        content = result.get("content", [])
        texts = [p["text"] for p in content if p.get("type") == "text" and "text" in p]
        if texts:
            raw = "\n".join(texts)
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    logger.info("[howtocook] tool=%s parsed_result keys=%s", tool_name, list(parsed.keys()))
                elif isinstance(parsed, list):
                    logger.info("[howtocook] tool=%s parsed_result list len=%d", tool_name, len(parsed))
                else:
                    logger.info("[howtocook] tool=%s parsed_result type=%s", tool_name, type(parsed).__name__)
                return parsed
            except json.JSONDecodeError:
                logger.info("[howtocook] tool=%s text_result len=%d", tool_name, len(raw))
                return {"text": raw}
        return result

    @staticmethod
    def _parse_sse(text: str) -> Dict[str, Any]:
        """Extract the last result payload from an SSE stream."""
        last: Dict[str, Any] = {}
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                    if "result" in payload or "error" in payload:
                        last = payload
                except json.JSONDecodeError:
                    pass
        return last
