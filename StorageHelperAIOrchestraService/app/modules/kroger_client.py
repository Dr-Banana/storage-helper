"""
Kroger Public API client — real US grocery prices per store.

Docs: https://developer.kroger.com
Flow:
  1. OAuth2 client-credentials token (Basic auth, scope=product.compact)
  2. GET /locations?filter.zipCode.near=<zip>          → locationId
  3. GET /products?filter.term=<term>&filter.locationId=<id>  → price

MOCK mode: when client_id/secret are not configured the client returns
deterministic fake prices so the pricing feature is fully testable without
live credentials. Everything downstream (aggregation, agent tools, UI) works
identically; only the numbers are synthetic and flagged via `mock=True`.
"""
import base64
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class KrogerLocation:
    location_id: str
    name: str
    chain: str = ""
    address: str = ""


@dataclass
class KrogerProduct:
    term: str                      # the search term this matched
    name: str                      # product description
    brand: str = ""
    size: str = ""
    regular_price: Optional[float] = None
    promo_price: Optional[float] = None
    product_id: str = ""
    mock: bool = False

    @property
    def price(self) -> Optional[float]:
        """Effective price: promo if present and lower, else regular."""
        if self.promo_price and self.promo_price > 0:
            if self.regular_price is None or self.promo_price < self.regular_price:
                return round(self.promo_price, 2)
        return round(self.regular_price, 2) if self.regular_price is not None else None


# Rough mock prices (USD) for common grocery items — keeps demo numbers sane.
_MOCK_BASE = {
    "milk": 3.49, "egg": 3.99, "eggs": 3.99, "bread": 2.79, "butter": 4.49,
    "chicken": 6.99, "beef": 8.99, "pork": 5.99, "rice": 9.99, "tomato": 2.49,
    "onion": 1.29, "garlic": 0.79, "ginger": 1.99, "potato": 3.49, "carrot": 1.79,
    "tofu": 2.29, "soy sauce": 3.99, "oil": 7.49, "salt": 1.19, "sugar": 2.99,
    "flour": 3.29, "cheese": 4.99, "spinach": 2.99, "broccoli": 2.49, "scallion": 1.49,
}


class KrogerClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.kroger.com/v1",
        timeout: float = 15.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self.mock = not (client_id and client_secret)
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        if self.mock:
            logger.info("[kroger] running in MOCK mode (no credentials configured)")
        else:
            logger.info("[kroger] configured with live credentials base=%s", self._base)

    # ── Auth ────────────────────────────────────────────────────────────────
    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        creds = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/connect/oauth2/token",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials", "scope": "product.compact"},
            )
            resp.raise_for_status()
            data = resp.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 1800))
        return self._token

    # ── Locations ───────────────────────────────────────────────────────────
    async def find_locations(self, zip_code: str, limit: int = 3) -> List[KrogerLocation]:
        if self.mock:
            return [
                KrogerLocation(
                    location_id=f"MOCK{zip_code}",
                    name="Ralphs (mock store)",
                    chain="RALPHS",
                    address=f"Near {zip_code}",
                )
            ]
        try:
            token = await self._get_token()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base}/locations",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"filter.zipCode.near": zip_code, "filter.limit": limit},
                )
                resp.raise_for_status()
                data = resp.json()
            out = []
            for loc in data.get("data", []):
                addr = loc.get("address", {}) or {}
                out.append(KrogerLocation(
                    location_id=loc.get("locationId", ""),
                    name=loc.get("name", ""),
                    chain=loc.get("chain", ""),
                    address=", ".join(
                        p for p in [addr.get("addressLine1"), addr.get("city"), addr.get("state")] if p
                    ),
                ))
            return out
        except Exception as exc:
            logger.error("[kroger] find_locations failed: %s", exc)
            return []

    # ── Products ────────────────────────────────────────────────────────────
    async def search_product(
        self, term: str, location_id: str, limit: int = 5
    ) -> Optional[KrogerProduct]:
        """Return the best-matching product (with price) for a search term."""
        if self.mock or (location_id or "").startswith("MOCK"):
            return self._mock_product(term)
        try:
            token = await self._get_token()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base}/products",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "filter.term": term,
                        "filter.locationId": location_id,
                        "filter.limit": limit,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            # Pick the first product that has a price.
            for prod in data.get("data", []):
                items = prod.get("items", []) or []
                price = (items[0].get("price", {}) if items else {}) or {}
                regular = price.get("regular")
                promo = price.get("promo")
                if regular is not None or promo:
                    return KrogerProduct(
                        term=term,
                        name=prod.get("description", term),
                        brand=prod.get("brand", ""),
                        size=(items[0].get("size", "") if items else ""),
                        regular_price=regular,
                        promo_price=promo,
                        product_id=prod.get("productId", ""),
                    )
            return None
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text[:200]
            except Exception:
                pass
            logger.warning(
                "[kroger] search_product(%r) → %s %s",
                term, exc.response.status_code, body,
            )
            return None
        except Exception as exc:
            logger.error("[kroger] search_product(%r) failed: %s", term, exc)
            return None

    # ── Mock helpers ────────────────────────────────────────────────────────
    def _mock_product(self, term: str) -> KrogerProduct:
        key = term.lower().strip()
        base = None
        for k, v in _MOCK_BASE.items():
            if k in key:
                base = v
                break
        if base is None:
            # Deterministic pseudo-price in [1.49, 9.49] from the term hash.
            h = int(hashlib.md5(key.encode()).hexdigest(), 16)
            base = round(1.49 + (h % 800) / 100.0, 2)
        return KrogerProduct(
            term=term,
            name=f"{term.title()} (mock)",
            brand="MockBrand",
            size="each",
            regular_price=base,
            promo_price=None,
            product_id=f"mock-{key}",
            mock=True,
        )
