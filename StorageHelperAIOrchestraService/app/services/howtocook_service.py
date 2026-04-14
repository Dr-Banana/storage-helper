"""
HowToCookService: Provides recipe data via the HowToCook-MCP server.

Completely independent of the AI pipeline — no knowledge of LLM, agents, or pipelines.
The AI agents consume this service as an optional data source and always fall back
gracefully when a recipe is not found or the MCP server is unavailable.

Relies on https://github.com/worryzyy/HowToCook-mcp running with --transport http.
The MCP server bundles all_recipes.json (structured data from Anduin2017/HowToCook)
and exposes it via five tools over StreamableHTTP at /mcp.

Lifecycle:
  initialize()            — fetch full catalog from MCP at startup, cache it
  start_background_sync() — asyncio task: refresh catalog every N hours
  shutdown()              — cancel background task

Public API (called by AI agents):
  find_recipe(dish_name)  → Optional[Dict]  full recipe dict for a dish (async)
  get_dish_catalog()      → List[Dict]      all dishes as seed-library-format dicts (sync, cached)
  lookup_dish(name)       → Optional[Dict]  single catalog entry by name (sync, cached)
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── FoodieService debug error codes ──────────────────────────────────────────
# Search these codes in logs to diagnose FoodieService-related failures.
FOODIE_ERR_001 = "FOODIE_ERR_001"  # MCP unreachable on startup (connection refused / timeout)
FOODIE_ERR_002 = "FOODIE_ERR_002"  # Catalog empty after successful MCP connection
FOODIE_ERR_003 = "FOODIE_ERR_003"  # MCP unreachable during runtime recipe lookup
FOODIE_ERR_004 = "FOODIE_ERR_004"  # Seed library fell back to seed_dishes.json (HowToCook unavailable)
FOODIE_ERR_005 = "FOODIE_ERR_005"  # Cooking steps generated without HowToCook reference (pure LLM)

# ── HowToCook category → seed-library metadata ───────────────────────────────
# (cuisine_l1, cuisine_l2, seed_category)
_CATEGORY_MAP: Dict[str, Tuple[str, str, str]] = {
    "荤菜":  ("Chinese", "Home Cooking",   "Meat"),
    "水产":  ("Chinese", "Seafood",         "Seafood"),
    "素菜":  ("Chinese", "Vegetarian",      "Vegetable"),
    "早餐":  ("Chinese", "Breakfast",       "Staple"),
    "主食":  ("Chinese", "Staples",         "Staple"),
    "汤羹":  ("Chinese", "Soups & Congee",  "Soup"),
    "甜品":  ("Chinese", "Desserts",        "Dessert"),
    "半成品加工": ("Chinese", "Semi-processed", "Other"),
    "饮料":  ("Chinese", "Beverages",       "Other"),
}


class HowToCookService:
    """Provides recipe lookup and dish catalog via the HowToCook-MCP server."""

    def __init__(self, mcp_url: str, refresh_interval_hours: int = 24) -> None:
        self.mcp_url = mcp_url
        self.refresh_interval = max(refresh_interval_hours, 1) * 3600
        self._catalog: List[Dict[str, Any]] = []
        self._initialized = False
        self._sync_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Fetch the full dish catalog from MCP and cache it."""
        try:
            self._catalog = await self._fetch_full_catalog()
            self._initialized = True
            self._invalidate_seed_cache()
            if not self._catalog:
                logger.warning(
                    "[%s] FoodieService connected but returned an empty catalog — "
                    "meal recommendations will fall back to seed_dishes.json. "
                    "Check that howtocook-mcp container is healthy at %s",
                    FOODIE_ERR_002, self.mcp_url,
                )
            else:
                logger.info("[HowToCook] Initialized: %d catalog entries from %s", len(self._catalog), self.mcp_url)
        except Exception as exc:
            logger.warning(
                "[%s] FoodieService (HowToCook MCP) unreachable on startup — "
                "meal recommendations and recipe steps will fall back to defaults. "
                "URL: %s  Cause: %s  "
                "Fix: ensure FoodieService container is running (`docker ps | grep howtocook-mcp`).",
                FOODIE_ERR_001, self.mcp_url, exc,
            )
            self._initialized = True  # mark initialized so callers don't retry on every request

    async def start_background_sync(self) -> None:
        """Periodically refresh the catalog from MCP."""
        logger.info("[HowToCook] Background sync started (interval=%dh)", self.refresh_interval // 3600)
        while True:
            await asyncio.sleep(self.refresh_interval)
            try:
                new_catalog = await self._fetch_full_catalog()
                if new_catalog:
                    self._catalog = new_catalog
                    self._invalidate_seed_cache()
                    logger.info("[HowToCook] Catalog refreshed: %d entries", len(self._catalog))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[HowToCook] Catalog refresh failed: %s", exc)

    def shutdown(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()

    @staticmethod
    def _invalidate_seed_cache() -> None:
        try:
            from app.services.seed_library import invalidate_cache
            invalidate_cache()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public recipe / catalog API
    # ------------------------------------------------------------------

    async def find_recipe(self, dish_name: str) -> Optional[Dict[str, Any]]:
        """Fetch a full recipe by dish name from MCP.

        Returns a dict with keys: dish_name, ingredients, steps, tools,
        estimated_time, raw_context (JSON string for LLM injection).
        Returns None if not found or MCP unavailable.
        """
        try:
            raw = await self._call_tool(
                "mcp_howtocook_getRecipeById", {"query": dish_name}
            )
            if not raw:
                return None
            data = json.loads(raw)
            # MCP may return a "no match" error object
            if "error" in data or "possibleMatches" in data:
                logger.debug("[HowToCook] No recipe for '%s': %s", dish_name, data.get("error", "fuzzy only"))
                return None
            return self._map_recipe(data)
        except Exception as exc:
            logger.debug("[HowToCook] find_recipe('%s') failed: %s", dish_name, exc)
            return None

    def get_dish_catalog(self) -> List[Dict[str, Any]]:
        """Return cached catalog (seed-library format). Sync — safe to call anywhere."""
        return list(self._catalog)

    def lookup_dish(self, name: str) -> Optional[Dict[str, Any]]:
        """Return catalog entry matching name_zh or name_en (with fuzzy fallback)."""
        name_lower = name.lower()
        for dish in self._catalog:
            if name in (dish["name_zh"], dish["name_en"]):
                return dish
            if name_lower in dish["name_zh"].lower() or dish["name_zh"].lower() in name_lower:
                return dish
        return None

    @property
    def is_available(self) -> bool:
        return self._initialized and bool(self._catalog)

    # ------------------------------------------------------------------
    # MCP communication
    # ------------------------------------------------------------------

    async def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        """Call a single MCP tool and return the text content of the first result."""
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from mcp import ClientSession
        except ImportError:
            logger.error("[HowToCook] 'mcp' package not installed — run: pip install mcp")
            return None

        try:
            async with streamablehttp_client(self.mcp_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    if result.content:
                        return result.content[0].text  # type: ignore[attr-defined]
            return None
        except Exception as exc:
            logger.warning(
                "[%s] FoodieService unreachable during runtime MCP call '%s' — "
                "falling back to pure LLM. URL: %s  Cause: %s",
                FOODIE_ERR_003, tool_name, self.mcp_url, exc,
            )
            return None

    async def _fetch_full_catalog(self) -> List[Dict[str, Any]]:
        """Build a seed-library-format catalog by calling getRecipesByCategory for each category."""
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from mcp import ClientSession
        except ImportError:
            logger.error("[HowToCook] 'mcp' package not installed")
            return []

        catalog: List[Dict[str, Any]] = []
        try:
            async with streamablehttp_client(self.mcp_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for category, (cuisine_l1, cuisine_l2, seed_cat) in _CATEGORY_MAP.items():
                        try:
                            result = await session.call_tool(
                                "mcp_howtocook_getRecipesByCategory", {"category": category}
                            )
                            if not result.content:
                                continue
                            recipes = json.loads(result.content[0].text)  # type: ignore[attr-defined]
                            for r in recipes:
                                entry = self._to_catalog_entry(r, cuisine_l1, cuisine_l2, seed_cat)
                                catalog.append(entry)
                        except Exception as exc:
                            logger.debug("[HowToCook] Category '%s' fetch failed: %s", category, exc)
        except Exception as exc:
            logger.warning(
                "[%s] FoodieService unreachable during catalog fetch — "
                "meal recommendations will use seed_dishes.json fallback. "
                "URL: %s  Cause: %s",
                FOODIE_ERR_001, self.mcp_url, exc,
            )

        return catalog

    # ------------------------------------------------------------------
    # Data mapping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_catalog_entry(
        recipe: Dict[str, Any],
        cuisine_l1: str,
        cuisine_l2: str,
        seed_category: str,
    ) -> Dict[str, Any]:
        """Convert a SimpleRecipe (from getRecipesByCategory) to seed-library format."""
        main_ing_names = [
            i["name"] for i in (recipe.get("ingredients") or [])[:6]
            if isinstance(i, dict) and i.get("name")
        ]
        return {
            "id": recipe.get("id", recipe.get("name", "")),
            "name_zh": recipe.get("name", ""),
            "name_en": recipe.get("name", ""),   # HowToCook is Chinese-only
            "cuisine_l1": cuisine_l1,
            "cuisine_l2": cuisine_l2,
            "flavor_profile": [],
            "main_ingredients": main_ing_names,
            "category": seed_category,
            "servings_base": 2,
        }

    @staticmethod
    def _map_recipe(data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a full Recipe (from getRecipeById) to our internal recipe dict."""
        ingredients = [
            {
                "name": i.get("name", ""),
                "quantity": i.get("text_quantity") or (
                    f"{i['quantity']}{i.get('unit', '')}" if i.get("quantity") else ""
                ),
            }
            for i in (data.get("ingredients") or [])
            if isinstance(i, dict) and i.get("name")
        ]
        steps = [
            s["description"]
            for s in sorted(data.get("steps") or [], key=lambda x: x.get("step", 0))
            if isinstance(s, dict) and s.get("description")
        ]
        total_min = data.get("total_time_minutes")
        estimated_time = f"{total_min} 分钟" if total_min else ""

        return {
            "dish_name": data.get("name", ""),
            "ingredients": ingredients,
            "steps": steps,
            "tools": [],
            "estimated_time": estimated_time,
            # Compact JSON string used as LLM context (replaces raw_markdown)
            "raw_context": json.dumps(
                {
                    "name": data.get("name"),
                    "description": data.get("description"),
                    "ingredients": [
                        {"name": i["name"], "quantity": i["quantity"]} for i in ingredients
                    ],
                    "steps": steps,
                    "estimated_time": estimated_time,
                },
                ensure_ascii=False,
            ),
        }


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_service_instance: Optional[HowToCookService] = None


def get_howtocook_service() -> HowToCookService:
    """Return the module-level singleton (constructed lazily, initialized on startup)."""
    global _service_instance
    if _service_instance is None:
        from app.core.config import settings
        _service_instance = HowToCookService(
            mcp_url=settings.HOWTOCOOK_MCP_URL,
            refresh_interval_hours=settings.HOWTOCOOK_SYNC_INTERVAL_HOURS,
        )
    return _service_instance
