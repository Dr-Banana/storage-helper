"""
SkillRunner — loads SKILL.md, builds payload, calls Gemini, returns parsed dict.

Single LLM call entrypoint for all meal_planning skills.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _load_skill(skill_dir: Path) -> Dict[str, Any]:
    """Parse SKILL.md and return {meta: dict, prompt: str}."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    meta: Dict[str, Any] = {}
    prompt = text

    m = _FRONTMATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                key, _, val = line.partition(":")
                val = val.strip()
                if val.replace(".", "", 1).isdigit():
                    meta[key.strip()] = float(val) if "." in val else int(val)
                else:
                    meta[key.strip()] = val
        prompt = text[m.end():]

    return {"meta": meta, "prompt": prompt.strip()}


async def run(
    skill_dir: Path,
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    gemini_api_url: str = "",
    language: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Execute a skill: load SKILL.md → call Gemini → parse and return JSON.

    Args:
        skill_dir:      path to the skill directory (must contain SKILL.md)
        user_message:   current user message or a pre-built JSON string
        history:        conversation history [{"role": "user"|"assistant", "content": str}]
        gemini_api_url: full Gemini generateContent URL including API key

    Returns:
        Parsed dict from LLM response, or None on failure.
    """
    skill = _load_skill(skill_dir)
    meta = skill["meta"]
    system_prompt = skill["prompt"]
    if language:
        system_prompt += f"\n\nIMPORTANT: Always respond in this language: {language}"

    contents: List[Dict] = []
    for msg in (history or [])[-8:]:
        role = "user" if msg.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "temperature": meta.get("temperature", 0.0),
            "maxOutputTokens": meta.get("max_tokens", 512),
            "responseMimeType": "application/json",
        },
    }

    skill_name = meta.get("name", skill_dir.name)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                gemini_api_url,
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        parts = (
            (data.get("candidates") or [{}])[0]
            .get("content", {})
            .get("parts") or []
        )
        raw = parts[0].get("text", "").strip() if parts else ""
        if not raw:
            logger.warning("[skill:%s] empty response", skill_name)
            return None

        # Try direct parse first, then fall back to extracting the first {...} block.
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("[skill:%s] no JSON in response: %r", skill_name, raw[:120])
            return None

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.warning("[skill:%s] JSON parse failed: %s | raw: %r", skill_name, exc, raw[:120])
            return None

    except Exception as exc:
        logger.warning("[skill:%s] failed: %s", skill_name, exc)
        return None
