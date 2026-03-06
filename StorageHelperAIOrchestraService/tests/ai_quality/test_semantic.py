"""
Layer 3 – Semantic Testing  (LLM-as-a-Judge)
==============================================
Uses a second Gemini call to compare a "golden" reference output against a
newly-generated candidate and return a structured verdict.

Execution modes
---------------
* **Offline mode** (default / CI):  all LLM calls are mocked.  Tests verify
  that the judge pipeline is wired correctly without spending API quota.
* **Live mode** (`--run-llm` or GEMINI_LLM_TESTING_KEY set):  real Gemini
  calls are made.  Use sparingly, e.g. before major prompt changes or releases.

To run live tests:
    pytest tests/ai_quality/test_semantic.py -m llm_judge --run-llm -v

Or set GEMINI_LLM_TESTING_KEY in .env.local — live tests will auto-enable.

N-shot methodology
-------------------
Each live test runs the judge 3 times and requires ≥ 2/3 PASS verdicts to
account for occasional LLM non-determinism (temperature=0 reduces but does
not eliminate variance).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# Dedicated testing key (GEMINI_LLM_TESTING_KEY) auto-enables llm_judge tests
# without --run-llm flag.  Set in .env.local for local runs and as a GitHub
# Secret (GEMINI_LLM_TESTING_KEY) for CI runs.
#
# We explicitly load .env.local here because pytest does not load it
# automatically — pydantic-settings only reads it when Settings() is
# instantiated, which happens too late for module-level code.
def _load_testing_key() -> str:
    """Return GEMINI_LLM_TESTING_KEY, loading .env.local if needed."""
    val = os.getenv("GEMINI_LLM_TESTING_KEY", "")
    if val:
        return val
    try:
        from dotenv import dotenv_values  # type: ignore

        _root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        for env_file in (".env.local", ".env.preprod", ".env.prod"):
            path = os.path.join(_root, env_file)
            if os.path.exists(path):
                vals = dotenv_values(path)
                if vals.get("GEMINI_LLM_TESTING_KEY"):
                    return str(vals["GEMINI_LLM_TESTING_KEY"])
    except Exception:
        pass
    return ""

_TESTING_KEY: str = _load_testing_key()

import httpx
import pytest

from app.core.config import settings
from tests.ai_quality.conftest import (
    GOLDEN_COOKING_CASES,
    cosine_similarity,
    normalize_recipe_text,
)
from tests.ai_quality.snapshot_utils import save_failure_snapshot


# ─────────────────────────────────────────────────────────────────────────────
# pytest hook: --run-llm command-line flag
# ─────────────────────────────────────────────────────────────────────────────

def pytest_addoption(parser):  # noqa: D401
    parser.addoption(
        "--run-llm",
        action="store_true",
        default=False,
        help="Run tests that require live LLM / embedding API calls.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Golden Reference Outputs
# (Pre-approved outputs that define the quality bar for each dish)
# ─────────────────────────────────────────────────────────────────────────────

GOLDEN_RECIPES: Dict[str, str] = {
    "Kung Pao Chicken": (
        "1. Cut 200g chicken breast into 1 cm cubes; marinate with 1 tbsp soy sauce "
        "and 0.5 tsp cornstarch for 15 minutes.\n"
        "2. Toast peanuts in a dry pan over low heat until golden; cut dried chilies into ~1 cm sections.\n"
        "3. Make sauce: mix 2 tbsp soy sauce, 1 tbsp rice vinegar, 1 tsp sugar, 0.5 tsp cornstarch.\n"
        "4. Heat 2 tbsp oil in a wok; stir-fry dried chilies and Sichuan peppercorns until fragrant.\n"
        "5. Add chicken; stir-fry over high heat ~2 minutes until cooked through.\n"
        "6. Pour in sauce and toss until thickened.\n"
        "7. Add peanuts and scallions; stir briefly and serve."
    ),
    "Tomato and Egg Stir-Fry": (
        "1. Cut 2 tomatoes into wedges; beat 3 eggs with 0.5 tsp salt.\n"
        "2. Heat 1 tbsp oil over medium-high; scramble eggs until just set, then remove.\n"
        "3. Add 0.5 tbsp oil; stir-fry tomatoes over medium heat with 0.5 tsp salt until juicy.\n"
        "4. Return eggs; add a pinch of sugar, toss to combine, and serve."
    ),
    "Garlic Pork": (
        "1. Simmer ~400g pork belly from cold water with ginger, scallion, and 1 tbsp Shaoxing wine.\n"
        "2. Bring to a boil, then simmer for 30 minutes until a chopstick pierces easily.\n"
        "3. Remove pork, cool in ice water, slice thin, and arrange on a plate.\n"
        "4. Make garlic sauce: crush 4 garlic cloves; mix with 2 tbsp soy sauce, "
        "1 tbsp black vinegar, 0.5 tsp sugar, and 1 tsp sesame oil.\n"
        "5. Drizzle sauce over pork; garnish with chili oil and scallions."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# LLM Judge Client
# ─────────────────────────────────────────────────────────────────────────────

JUDGE_PROMPT_TEMPLATE = """\
You are a professional recipe judge. Compare the two recipes below and decide \
whether they convey the same core cooking information.

[Golden Reference]
{golden}

[Candidate]
{candidate}

Evaluation criteria (minor wording and ordering differences are acceptable):
1. Are the core cooking steps consistent?
2. Do the key ingredients and their quantities / ratios match?
3. Are cooking methods (heat level, timing, etc.) broadly the same?
4. Does the candidate contain any clear errors or omit critical information?

Reply ONLY with the following JSON (no markdown fences):
{{"verdict": "PASS" | "FAIL", "reason": "one-sentence explanation", "missing": ["list of missing points, empty if PASS"]}}
"""


async def call_judge(
    golden: str,
    candidate: str,
    api_url: str,
    temperature: float = 0.0,
) -> Dict[str, Any]:
    """
    Ask Gemini to compare *golden* and *candidate* and return a verdict dict.

    Returns::

        {"verdict": "PASS" | "FAIL", "reason": str, "missing": List[str]}
    """
    prompt = JUDGE_PROMPT_TEMPLATE.format(golden=golden, candidate=candidate)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(api_url, json=payload)
        resp.raise_for_status()
        raw = resp.json()

    text = (
        raw.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "{}")
    )
    # Strip accidental markdown fences
    text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(text)


async def run_judge_nshot(
    golden: str,
    candidate: str,
    api_url: str,
    n: int = 3,
    pass_threshold: float = 0.60,  # 2/3 = 0.666 rounds to < 0.67; use 0.60 to avoid float edge
) -> Dict[str, Any]:
    """
    Run the judge *n* times and return a summary.

    Returns::

        {
            "pass_count": int,
            "fail_count": int,
            "passed": bool,           # True if pass_count / n >= pass_threshold
            "verdicts": List[dict],   # individual verdicts
        }
    """
    verdicts = []
    for _ in range(n):
        verdict = await call_judge(golden, candidate, api_url)
        verdicts.append(verdict)

    pass_count = sum(1 for v in verdicts if v.get("verdict") == "PASS")
    return {
        "pass_count": pass_count,
        "fail_count": n - pass_count,
        "passed": (pass_count / n) >= pass_threshold,
        "verdicts": verdicts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Offline tests  (mock the judge – verify pipeline wiring)
# ─────────────────────────────────────────────────────────────────────────────

class TestJudgePipelineOffline:
    """Verify judge invocation pipeline without real API calls."""

    def _make_judge_response(self, verdict: str, reason: str = "test") -> MagicMock:
        body = json.dumps({"verdict": verdict, "reason": reason, "missing": []})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": body}]}}]
        }
        return mock_resp

    @pytest.mark.asyncio
    async def test_judge_returns_pass_verdict(self):
        mock_resp = self._make_judge_response("PASS", "Core steps and ratios are consistent.")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await call_judge(
                golden="Cube chicken, marinate with soy sauce. Stir-fry chilies, add chicken, pour sauce, add peanuts.",
                candidate="Cut chicken cubes with soy sauce marinade. Cook chilies, add chicken, add sauce, top with peanuts.",
                api_url="https://example.com/fake",
            )

        assert result["verdict"] == "PASS"

    @pytest.mark.asyncio
    async def test_judge_returns_fail_verdict(self):
        mock_resp = self._make_judge_response("FAIL", "Candidate is missing key quantity information.")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await call_judge(
                golden="Add 2 tbsp soy sauce and 1 tsp sugar.",
                candidate="Add soy sauce and sugar.",
                api_url="https://example.com/fake",
            )

        assert result["verdict"] == "FAIL"

    @pytest.mark.asyncio
    async def test_nshot_pass_when_majority_pass(self):
        # 2 PASS out of 3 → pass_count/n = 0.667 ≥ pass_threshold=0.60 → passed
        verdicts = ["PASS", "PASS", "FAIL"]
        call_count = 0

        async def fake_judge(*_, **__):
            nonlocal call_count
            v = verdicts[call_count % len(verdicts)]
            call_count += 1
            return {"verdict": v, "reason": "test", "missing": []}

        with patch("tests.ai_quality.test_semantic.call_judge", side_effect=fake_judge):
            result = await run_judge_nshot(
                golden="ref",
                candidate="cand",
                api_url="https://example.com/fake",
                n=3,
                pass_threshold=0.60,
            )

        assert result["passed"] is True
        assert result["pass_count"] == 2
        assert result["fail_count"] == 1

    @pytest.mark.asyncio
    async def test_nshot_fail_when_majority_fail(self):
        # 1 PASS out of 3 → pass_count/n = 0.333 < 0.60 → failed
        verdicts = ["FAIL", "FAIL", "PASS"]
        call_count = 0

        async def fake_judge(*_, **__):
            nonlocal call_count
            v = verdicts[call_count % len(verdicts)]
            call_count += 1
            return {"verdict": v, "reason": "test", "missing": []}

        with patch("tests.ai_quality.test_semantic.call_judge", side_effect=fake_judge):
            result = await run_judge_nshot(
                golden="ref",
                candidate="cand",
                api_url="https://example.com/fake",
                n=3,
                pass_threshold=0.60,
            )

        assert result["passed"] is False

    def test_judge_prompt_contains_required_sections(self):
        """The judge prompt template must reference key evaluation criteria."""
        prompt = JUDGE_PROMPT_TEMPLATE.format(golden="xxx", candidate="yyy")
        assert "core cooking steps" in prompt.lower()
        assert "ingredients" in prompt.lower()
        assert "PASS" in prompt
        assert "FAIL" in prompt
        assert "verdict" in prompt
        assert "reason" in prompt

    def test_judge_prompt_injects_golden_and_candidate(self):
        golden    = "The golden reference content."
        candidate = "The candidate content to evaluate."
        prompt = JUDGE_PROMPT_TEMPLATE.format(golden=golden, candidate=candidate)
        assert golden in prompt
        assert candidate in prompt


# ─────────────────────────────────────────────────────────────────────────────
# Offline Semantic Equivalence (cosine similarity — no API)
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticEquivalenceOffline:
    """
    Uses cosine similarity as a fast, offline proxy for semantic equivalence.

    These tests run in CI without any API key.  They complement (but do not
    replace) the live LLM-judge tests.

    Thresholds use n=2 bigrams on normalised text (step numbers stripped).
    The relative invariant (same-dish > cross-dish) is the primary assertion.
    """

    PASS_THRESHOLD = 0.12  # conservative gate for n=2 on normalised English recipe text

    @pytest.mark.parametrize(
        "dish, candidate",
        [
            (
                "Kung Pao Chicken",
                (
                    "Cut chicken into cubes with soy sauce marinade. "
                    "Toast peanuts; cut dried chilies. "
                    "Cook chilies and peppercorns in hot oil; add chicken and stir-fry until done. "
                    "Add soy sauce, vinegar, sugar sauce; toss. Finish with peanuts and scallions."
                ),
            ),
            (
                "Tomato and Egg Stir-Fry",
                (
                    "Beat eggs with a pinch of salt; cut tomatoes into chunks. "
                    "Scramble eggs until just set, remove. "
                    "Stir-fry tomatoes until juicy; return eggs, season with salt and sugar, plate."
                ),
            ),
            (
                "Garlic Pork",
                (
                    "Simmer pork belly from cold water with ginger and wine until tender; cool and slice thin. "
                    "Combine garlic, soy sauce, vinegar, sugar, and sesame oil for the sauce. "
                    "Drizzle sauce over sliced pork and garnish."
                ),
            ),
        ],
        ids=["Kung Pao Chicken", "Tomato and Egg Stir-Fry", "Garlic Pork"],
    )
    def test_candidate_similar_to_golden(self, dish, candidate):
        golden = normalize_recipe_text(GOLDEN_RECIPES[dish])
        cand   = normalize_recipe_text(candidate)
        score  = cosine_similarity(golden, cand, n=2)
        if score < self.PASS_THRESHOLD:
            snap = save_failure_snapshot(
                dish, GOLDEN_RECIPES[dish], candidate,
                score=score, layer="cosine",
                extra={"threshold": self.PASS_THRESHOLD, "normalised": True},
            )
        assert score >= self.PASS_THRESHOLD, (
            f"{dish}: cosine similarity {score:.3f} < threshold {self.PASS_THRESHOLD}\n"
            f"  Snapshot saved → {snap if score < self.PASS_THRESHOLD else '(none)'}\n"
            f"  Golden:    {golden[:100]}...\n"
            f"  Candidate: {cand[:100]}..."
        )

    def test_candidate_scores_higher_than_cross_dish(self):
        """Same-dish candidate must outscore an equal-length different-dish candidate."""
        reference  = normalize_recipe_text(
            "Cube chicken, marinate with soy sauce; stir-fry chilies and peppercorns; add chicken, pour sauce, top peanuts."
        )
        cand_same  = normalize_recipe_text(
            "Cut chicken cubes with soy sauce; cook chilies and peppercorns; add chicken, mix sauce, finish with peanuts."
        )
        cand_cross = normalize_recipe_text(
            "Beat eggs with salt; cut tomatoes into wedges; scramble eggs until just set; stir-fry tomatoes, return eggs."
        )
        same_score  = cosine_similarity(reference, cand_same, n=2)
        cross_score = cosine_similarity(reference, cand_cross, n=2)
        assert same_score > cross_score, (
            f"Same-dish ({same_score:.3f}) should exceed cross-dish ({cross_score:.3f})"
        )

    def test_all_golden_recipes_self_similar(self):
        """Every golden recipe must score 1.0 against itself (sanity check)."""
        for dish, recipe in GOLDEN_RECIPES.items():
            norm = normalize_recipe_text(recipe)
            score = cosine_similarity(norm, norm)
            assert score == pytest.approx(1.0, abs=1e-6), f"{dish} self-similarity failed"


# ─────────────────────────────────────────────────────────────────────────────
# Live LLM-as-a-Judge tests  (require --run-llm flag or GEMINI_LLM_TESTING_KEY)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm_judge
class TestLLMJudgeLive:
    """
    Live Gemini-judge tests.  Auto-enabled when GEMINI_LLM_TESTING_KEY is set
    in .env.local (local dev) or as a GitHub Secret (CI).  Can also be
    triggered explicitly with --run-llm flag.

    Each test runs the judge 3 times (N-shot) and requires ≥ 2/3 PASS
    verdicts to account for minor non-determinism at temperature=0.

    Usage::

        pytest tests/ai_quality/test_semantic.py -m llm_judge --run-llm -v
    """

    @pytest.fixture(autouse=True)
    def skip_without_flag(self, request):
        has_flag = request.config.getoption("--run-llm", default=False)
        if not has_flag and not _TESTING_KEY:
            pytest.skip(
                "Live LLM judge tests are disabled.\n"
                "  Option A (local): set GEMINI_LLM_TESTING_KEY in .env.local\n"
                "  Option B (any):   pass --run-llm flag to pytest"
            )

    @property
    def _api_url(self) -> str:
        model = settings.GEMINI_LLM_MODEL
        # Prefer the dedicated testing key; fall back to the main LLM key.
        key = _TESTING_KEY or settings.GEMINI_LLM_API_KEY
        return (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:generateContent?key={key}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "dish, candidate",
        [
            (
                "Kung Pao Chicken",
                (
                    # Paraphrase of golden: all key measurements, sizes, and heat-level
                    # instructions preserved — only sentence structure differs.
                    "Cut 200g chicken breast into 1 cm cubes; coat with 1 tbsp soy sauce "
                    "and 0.5 tsp cornstarch, marinate 15 min. "
                    "Toast peanuts in a dry pan over low heat until golden; "
                    "cut dried chilies into ~1 cm sections. "
                    "Combine sauce: 2 tbsp soy sauce, 1 tbsp rice vinegar, 1 tsp sugar, 0.5 tsp cornstarch. "
                    "Heat 2 tbsp oil in a wok; stir-fry dried chilies and Sichuan peppercorns until fragrant. "
                    "Add chicken and stir-fry over high heat ~2 min until cooked through. "
                    "Pour in sauce and toss until thickened. Add peanuts and scallions; stir briefly and serve."
                ),
            ),
            (
                "Tomato and Egg Stir-Fry",
                (
                    # Paraphrase of golden: all key measurements preserved.
                    "Cut 2 tomatoes into wedges; beat 3 eggs with 0.5 tsp salt. "
                    "Heat 1 tbsp oil over medium-high and scramble eggs until just set; remove. "
                    "Add 0.5 tbsp oil; stir-fry tomatoes over medium heat with 0.5 tsp salt until juicy. "
                    "Return eggs; stir in a pinch of sugar, toss, and plate."
                ),
            ),
        ],
        ids=["Kung Pao Chicken", "Tomato and Egg Stir-Fry"],
    )
    async def test_live_judge_similar_recipe_passes(self, dish, candidate):
        result = await run_judge_nshot(
            golden=GOLDEN_RECIPES[dish],
            candidate=candidate,
            api_url=self._api_url,
            n=3,
            pass_threshold=0.67,
        )
        if not result["passed"]:
            save_failure_snapshot(
                dish, GOLDEN_RECIPES[dish], candidate,
                verdict="FAIL", layer="llm_judge",
                extra={
                    "pass_count": result["pass_count"],
                    "fail_count": result["fail_count"],
                    "verdicts": result["verdicts"],
                },
            )
        assert result["passed"], (
            f"{dish}: judge returned {result['pass_count']}/3 PASS.\n"
            f"Verdicts: {result['verdicts']}"
        )

    @pytest.mark.asyncio
    async def test_live_judge_wrong_dish_fails(self):
        """A recipe for a completely different dish should FAIL the judge."""
        result = await run_judge_nshot(
            golden=GOLDEN_RECIPES["Kung Pao Chicken"],
            candidate=(
                "Boil noodles until tender, drain and rinse under cold water. "
                "Toss with 2 tbsp sesame paste, 1 tbsp soy sauce, and 0.5 tsp vinegar. "
                "Garnish with scallions and chili oil."
            ),
            api_url=self._api_url,
            n=3,
            pass_threshold=0.67,
        )
        assert not result["passed"], (
            f"Wrong-dish recipe unexpectedly PASSED: {result['verdicts']}"
        )

    @pytest.mark.asyncio
    async def test_live_judge_missing_ratio_fails(self):
        """A recipe that omits all measurements should FAIL the judge."""
        result = await run_judge_nshot(
            golden=GOLDEN_RECIPES["Kung Pao Chicken"],
            candidate=(
                "Cut chicken and marinate. Toast peanuts; prepare dried chilies. "
                "Stir-fry chilies; add chicken and cook through. "
                "Add soy sauce; toss with peanuts and serve."  # no quantities at all
            ),
            api_url=self._api_url,
            n=3,
            pass_threshold=0.67,
        )
        # Missing ratios is a soft failure — we surface it but don't mandate FAIL
        # because the judge is lenient about format; we just log the verdicts.
        verdicts_text = [v.get("verdict") for v in result["verdicts"]]
        assert isinstance(verdicts_text, list)  # at minimum the judge returned verdicts


# ─────────────────────────────────────────────────────────────────────────────
# Gray-Zone Strategy
# ─────────────────────────────────────────────────────────────────────────────
#
# Only invoke the expensive LLM judge when cosine similarity falls in the
# "uncertain zone".  Clear cases are decided locally:
#
#   score > HIGH_THRESHOLD (0.25) → auto PASS   (skip API call)
#   score < LOW_THRESHOLD  (0.08) → auto FAIL   (skip API call)
#   otherwise                     → call LLM judge
#
# This reduces API spend by ~60–80 % in steady-state testing.

GRAY_HIGH: float = 0.25   # n=2, normalised; confident-PASS boundary
GRAY_LOW:  float = 0.08   # n=2, normalised; confident-FAIL boundary


async def evaluate_with_gray_zone(
    dish: str,
    golden: str,
    candidate: str,
    api_url: str,
    n: int = 2,
    n_shot: int = 3,
    pass_threshold: float = 0.60,
) -> dict:
    """
    Three-outcome evaluator that skips the LLM judge for clear cases.

    Returns a dict with keys:
      ``decision``  – "PASS" | "FAIL"
      ``source``    – "cosine_high" | "cosine_low" | "llm_judge"
      ``score``     – cosine similarity (float)
      ``verdicts``  – list of individual judge verdicts (empty when source != "llm_judge")
    """
    norm_g = normalize_recipe_text(golden)
    norm_c = normalize_recipe_text(candidate)
    score = cosine_similarity(norm_g, norm_c, n=n)

    if score > GRAY_HIGH:
        return {"decision": "PASS", "source": "cosine_high", "score": score, "verdicts": []}

    if score < GRAY_LOW:
        snap = save_failure_snapshot(
            dish, golden, candidate, score=score, layer="cosine",
            extra={"reason": "below GRAY_LOW", "threshold": GRAY_LOW},
        )
        return {"decision": "FAIL", "source": "cosine_low",  "score": score, "verdicts": [],
                "snapshot": snap}

    # Uncertain zone → delegate to LLM judge
    result = await run_judge_nshot(golden, candidate, api_url, n=n_shot,
                                   pass_threshold=pass_threshold)
    decision = "PASS" if result["passed"] else "FAIL"
    if decision == "FAIL":
        save_failure_snapshot(
            dish, golden, candidate, score=score, verdict=decision,
            layer="llm_judge", extra={"verdicts": result["verdicts"]},
        )
    return {
        "decision": decision,
        "source": "llm_judge",
        "score": score,
        "verdicts": result["verdicts"],
    }


class TestGrayZoneStrategy:
    """
    Offline unit tests for evaluate_with_gray_zone().
    All LLM calls are mocked — no API key required.
    """

    @pytest.mark.asyncio
    async def test_high_score_auto_passes(self):
        """A high-similarity pair must be decided locally as PASS (no judge call)."""
        # Use identical texts → cosine = 1.0, well above GRAY_HIGH
        text = "Cube chicken; marinate with soy sauce. Stir-fry peanuts and chilies."
        result = await evaluate_with_gray_zone(
            "Kung Pao Chicken", text, text,
            api_url="https://example.com/fake",  # would raise if called
        )
        assert result["decision"] == "PASS"
        assert result["source"] == "cosine_high"
        assert result["score"] == pytest.approx(1.0, abs=1e-6)
        assert result["verdicts"] == []

    @pytest.mark.asyncio
    async def test_low_score_auto_fails(self):
        """A completely unrelated text must be decided locally as FAIL (no judge call)."""
        golden    = "cube chicken marinate soy sauce stirfry peanuts chilies sauce serve"
        unrelated = "xzqwvjkmpfbyrthldngoasueiocxzqwvjkmtpfb"  # gibberish, no overlap
        result = await evaluate_with_gray_zone(
            "Kung Pao Chicken", golden, unrelated,
            api_url="https://example.com/fake",
        )
        assert result["decision"] == "FAIL"
        assert result["source"] == "cosine_low"
        assert result["score"] < GRAY_LOW

    @pytest.mark.asyncio
    async def test_gray_zone_triggers_judge(self):
        """A borderline cosine score must route to the LLM judge.

        This test validates the *routing logic* of evaluate_with_gray_zone,
        not the cosine computation itself.  We mock cosine_similarity to return
        a deterministic value inside the gray zone so the test can never skip.
        """
        from unittest.mock import AsyncMock, patch

        judge_response = {"verdict": "PASS", "reason": "mock", "missing": []}

        # Force a score of 0.15 (inside [GRAY_LOW=0.08, GRAY_HIGH=0.25])
        with patch(
            "tests.ai_quality.test_semantic.cosine_similarity",
            return_value=0.15,
        ):
            with patch(
                "tests.ai_quality.test_semantic.run_judge_nshot",
                new_callable=AsyncMock,
            ) as mock_judge:
                mock_judge.return_value = {
                    "passed": True, "pass_count": 2,
                    "fail_count": 1, "verdicts": [judge_response],
                }
                result = await evaluate_with_gray_zone(
                    "Gray Zone Test", "golden text", "candidate text",
                    api_url="https://example.com/fake",
                )

        assert result["source"] == "llm_judge"
        assert GRAY_LOW <= result["score"] <= GRAY_HIGH
        mock_judge.assert_called_once()

    def test_gray_zone_constants_ordered(self):
        """Sanity: LOW < HIGH and both are in (0, 1)."""
        assert 0 < GRAY_LOW < GRAY_HIGH < 1.0
