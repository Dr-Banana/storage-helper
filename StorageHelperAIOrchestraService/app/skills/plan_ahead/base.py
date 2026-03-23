# -*- coding: utf-8 -*-
"""
BaseSkill — abstract base for all PlanAhead LLM skills.

A Skill encapsulates one focused LLM judgment:
  - SKILL_PROMPT  : the system prompt (class-level constant, easy to read/edit)
  - execute(...)  : calls the LLM and returns a typed dict
  - _call(...)    : low-level Gemini HTTP call shared by all skills

Benefits over inline prompt strings in the pipeline:
  1. Each prompt lives in ONE place — change it without touching pipeline logic.
  2. Skills are independently unit-testable (mock _call → test parsing logic).
  3. The pipeline becomes a thin orchestrator, not a prompt kitchen.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class LLMSkill(ABC):
    """
    Abstract base class for a single-purpose LLM skill.

    Subclasses must set:
      SKILL_NAME    — short identifier used in log messages
      SKILL_PROMPT  — the system prompt string

    Subclasses must implement:
      execute(...)  — call _call() and parse the raw text into a typed dict
    """

    SKILL_NAME: str = "skill"
    SKILL_PROMPT: str = ""

    # Generation defaults — subclasses may override per-skill
    TEMPERATURE: float = 0.0
    MAX_OUTPUT_TOKENS: int = 512
    # Set to "application/json" to use Gemini's structured JSON mode
    RESPONSE_MIME_TYPE: str = "application/json"
    # Optional Gemini responseSchema dict; set to None to skip
    RESPONSE_SCHEMA: Optional[Dict[str, Any]] = None

    def __init__(self, gemini_api_url: str) -> None:
        self.gemini_api_url = gemini_api_url

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Run the skill and return a typed result dict."""

    # ------------------------------------------------------------------
    # Shared low-level call
    # ------------------------------------------------------------------

    async def _call(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        *,
        system_prompt_override: Optional[str] = None,
        timeout: float = 15.0,
    ) -> Optional[str]:
        """
        Call Gemini with the skill's system prompt and return the raw text.
        Returns None on network / API error (caller handles fallback).
        """
        if not self.gemini_api_url:
            return None

        system_text = system_prompt_override or self.SKILL_PROMPT
        contents: List[Dict[str, Any]] = []
        for msg in (history or [])[-6:]:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": user_message}]})

        gen_config: Dict[str, Any] = {
            "temperature": self.TEMPERATURE,
            "maxOutputTokens": self.MAX_OUTPUT_TOKENS,
        }
        if self.RESPONSE_MIME_TYPE:
            gen_config["responseMimeType"] = self.RESPONSE_MIME_TYPE
        if self.RESPONSE_SCHEMA:
            gen_config["responseSchema"] = self.RESPONSE_SCHEMA

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_text}]},
            "generationConfig": gen_config,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self.gemini_api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            candidates = data.get("candidates") or []
            if not candidates:
                logger.warning("[%s] No candidates in response", self.SKILL_NAME)
                return None
            parts = (candidates[0].get("content") or {}).get("parts") or []
            raw = parts[0].get("text", "").strip() if parts else ""
            return raw or None
        except Exception as exc:
            logger.warning("[%s] LLM call failed: %s", self.SKILL_NAME, exc)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
        """Extract the first JSON object from raw LLM text (handles markdown fences)."""
        if not raw:
            return None
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
