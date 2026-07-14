"""
Translate ingredient names to English grocery search terms.

The recipe source (HowToCook) is Chinese, but the price source (Kroger) is a
US/English grocery API that 400s on non-English terms. This bridges the two:
given a list of ingredient names, return an English search term for each.

Batched into a single Gemini call, and skipped entirely when every name is
already ASCII (so English recipes cost nothing).
"""
import json
import logging
import re
from typing import Dict, List

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _needs_translation(names: List[str]) -> bool:
    return any(any(ord(c) > 127 for c in n) for n in names)


async def to_english_terms(names: List[str]) -> Dict[str, str]:
    """
    Map each ingredient name to a simple English grocery search term.
    Falls back to an identity mapping when names are already English or on error.
    """
    names = [n for n in names if n]
    if not names or not _needs_translation(names):
        return {n: n for n in names}
    if not settings.GEMINI_LLM_API_KEY:
        logger.warning("[translate] no GEMINI_LLM_API_KEY — skipping translation")
        return {n: n for n in names}

    prompt = (
        "Translate each grocery ingredient into a simple English term a US "
        "supermarket search box would understand (generic, singular, no brand, "
        "no quantities). Examples: 五花肉->pork belly, 生抽->soy sauce, "
        "老抽->soy sauce, 葱->green onion, 蒜头->garlic, 姜片->ginger, "
        "白砂糖->sugar, 料酒->cooking wine, 五香粉->five spice powder, 盐->salt. "
        "Return ONLY a JSON object mapping each input string EXACTLY as given to "
        "its English term.\n"
        f"Inputs: {json.dumps(names, ensure_ascii=False)}"
    )
    try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.GEMINI_LLM_MODEL}:generateContent"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1024},
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url, json=payload, headers={"x-goog-api-key": settings.GEMINI_LLM_API_KEY}
            )
            resp.raise_for_status()
            data = resp.json()

        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        mapping = json.loads(match.group(0)) if match else {}
        result = {n: (str(mapping.get(n)).strip() if mapping.get(n) else n) for n in names}
        logger.info("[translate] %d term(s) → English", len(result))
        return result
    except Exception as exc:
        logger.error("[translate] failed, using originals: %s", exc)
        return {n: n for n in names}
