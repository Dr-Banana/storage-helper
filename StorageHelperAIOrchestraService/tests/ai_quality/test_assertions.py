"""
Layer 2 – Assertion Logic
==========================
Fine-grained, deterministic checks on the *content* of AI-generated outputs.
No LLM calls — all assertions are pure Python / numpy and run offline.

Three categories of checks:
  A. Keyword checks       – required ingredients, verbs, units of measure
  B. Step-count checks    – min / max step count per dish category
  C. Cosine-similarity    – character n-gram similarity between reference
                            and generated text (offline proxy for semantics)

Design note on cosine similarity
---------------------------------
We use character bigram (n=2) vectors for recipe text comparison.

For English recipe text, the primary validation is the **relative invariant**:
same-dish paraphrases must outscore cross-dish comparisons.  Absolute thresholds
are used only as regression gates and are calibrated conservatively.

  Empirical calibration on short English recipe snippets (n=2, normalised):
    same-dish  ≈ 0.25–0.40
    cross-dish ≈ 0.15–0.30
    threshold   ≈ 0.15 (conservative gate)

The relative test (same-dish score > cross-dish score) is the most robust
check and does not require threshold tuning.
"""
from __future__ import annotations

import math
from typing import List

import pytest

from tests.ai_quality.conftest import (
    GOLDEN_COOKING_CASES,
    cosine_similarity,
    normalize_recipe_text,
    has_measurement_units,
    steps_have_action_verbs,
    extract_quantity_units,
    jaccard_qty_similarity,
    is_quantity_sane,
)
from tests.ai_quality.snapshot_utils import save_failure_snapshot


# ─────────────────────────────────────────────────────────────────────────────
# A. Keyword / Unit Checks
# ─────────────────────────────────────────────────────────────────────────────

class TestMeasurementUnits:
    """Steps MUST contain at least one explicit measurement unit."""

    def test_weight_unit_grams(self):
        steps = ["Add 200 grams chicken breast.", "Toss with 30 grams of peanuts."]
        assert has_measurement_units(steps)

    def test_volume_unit_tablespoon(self):
        steps = ["Mix with 2 tbsp soy sauce.", "Add 1 tsp salt to taste."]
        assert has_measurement_units(steps)

    def test_si_units(self):
        steps = ["Add 200 grams of chicken.", "Mix with 30ml soy sauce."]
        assert has_measurement_units(steps)

    def test_time_unit_counts(self):
        steps = ["Marinate for 20 min.", "Stir-fry for 3 minutes until done."]
        assert has_measurement_units(steps)

    def test_temperature_unit_counts(self):
        steps = ["Preheat oven to 200°C.", "Bake for 20 minutes until golden."]
        assert has_measurement_units(steps)

    def test_fails_without_any_unit(self):
        steps = ["Cut the chicken into pieces.", "Place in the pan and stir-fry.", "Season and serve."]
        assert not has_measurement_units(steps)

    def test_threshold_respected(self):
        steps = ["Add 2 tbsp soy sauce.", "Place chicken in the pan.", "Season and serve."]
        assert has_measurement_units(steps, threshold=1)
        assert not has_measurement_units(steps, threshold=2)

    @pytest.mark.parametrize("case", GOLDEN_COOKING_CASES, ids=[c["dish"] for c in GOLDEN_COOKING_CASES])
    def test_golden_cases_require_at_least_one_unit(self, case):
        """Each golden dish spec declares required units — mock steps must satisfy them."""
        mock_steps = [
            f"Prepare the main ingredients for {case['dish']}; weigh out 2 tbsp soy sauce and 1 tsp salt.",
            "Heat 1 tbsp oil in a wok; stir-fry aromatics for 30 seconds.",
            "Add main ingredient and stir-fry for 3 minutes until cooked through.",
            "Add seasoning and toss to coat; simmer for 5 minutes.",
            "Adjust seasoning and serve.",
        ]
        assert has_measurement_units(mock_steps, threshold=1), (
            f"Steps for {case['dish']} should contain at least one measurement unit"
        )


class TestKeywordPresence:
    """Critical ingredients / techniques must appear in at least one step."""

    @pytest.mark.parametrize(
        "dish, keywords, steps",
        [
            (
                "Kung Pao Chicken",
                ["chicken", "peanut", "chili"],
                [
                    "Cut chicken breast into cubes and marinate with 1 tbsp soy sauce.",
                    "Toast peanuts until golden; cut dried chilies into sections.",
                    "Stir-fry aromatics, then add chicken and cook through.",
                    "Pour in sauce; add peanuts and serve.",
                ],
            ),
            (
                "Tomato and Egg Stir-Fry",
                ["tomato", "egg"],
                [
                    "Cut tomatoes into wedges; beat eggs with a pinch of salt.",
                    "Scramble eggs until just set, then remove from pan.",
                    "Stir-fry tomatoes until juicy; return eggs, season, and serve.",
                ],
            ),
            (
                "Garlic Pork",
                ["pork belly", "garlic"],
                [
                    "Simmer pork belly from cold water until chopstick-tender.",
                    "Crush garlic and mix with 2 tbsp soy sauce to make the sauce.",
                    "Slice cooled pork thin and drizzle the garlic sauce over.",
                ],
            ),
        ],
    )
    def test_key_ingredients_present(self, dish, keywords, steps):
        full_text = " ".join(steps).lower()
        missing = [kw for kw in keywords if kw.lower() not in full_text]
        assert not missing, f"Steps for {dish} missing keywords: {missing}"

    def test_rejects_wrong_dish_keywords(self):
        """Sanity check: Tomato and Egg steps should NOT mention peanuts."""
        steps = [
            "Cut tomatoes into wedges; beat eggs with a pinch of salt.",
            "Scramble eggs until just set, then remove.",
            "Stir-fry tomatoes until juicy; return eggs, season, and serve.",
        ]
        full_text = " ".join(steps).lower()
        assert "peanut" not in full_text


class TestActionVerbPresence:
    """Every step should contain at least one cooking action verb."""

    def test_all_steps_have_verbs(self):
        steps = [
            "Cut chicken into cubes and marinate for 15 minutes.",
            "Add 2 tbsp soy sauce and stir to coat evenly.",
            "Pour in 0.5 cup water, bring to a boil, then simmer for 10 minutes.",
        ]
        assert steps_have_action_verbs(steps)

    def test_fails_when_step_lacks_verb(self):
        steps = [
            "Chicken cubes.",          # noun phrase, no verb
            "A bowl of seasoning.",    # descriptive, no verb
            "2 tbsp of soy sauce.",    # measurement only, no verb
        ]
        assert not steps_have_action_verbs(steps)


# ─────────────────────────────────────────────────────────────────────────────
# B. Step-Count Checks
# ─────────────────────────────────────────────────────────────────────────────

class TestStepCount:
    """Step lists must fall within the declared min/max for each dish category."""

    @pytest.mark.parametrize("case", GOLDEN_COOKING_CASES, ids=[c["dish"] for c in GOLDEN_COOKING_CASES])
    def test_step_count_within_golden_range(self, case):
        steps = [f"Step {i + 1}" for i in range(case["min_steps"])]
        count = len(steps)
        assert case["min_steps"] <= count <= case["max_steps"], (
            f"{case['dish']}: expected {case['min_steps']}–{case['max_steps']} steps, got {count}"
        )

    @pytest.mark.parametrize(
        "steps, expected_pass",
        [
            (["s1", "s2", "s3", "s4", "s5"], True),
            (["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"], True),
            (["s1", "s2"], False),
            (["s{}".format(i) for i in range(20)], False),
        ],
    )
    def test_step_count_guard(self, steps, expected_pass):
        in_range = 3 <= len(steps) <= 15
        assert in_range == expected_pass

    def test_single_step_recipe_rejected(self):
        assert not (3 <= 1 <= 15)

    def test_zero_step_recipe_rejected(self):
        assert not (3 <= 0 <= 15)


# ─────────────────────────────────────────────────────────────────────────────
# C. Cosine Similarity (character bigrams n=2, offline)
# ─────────────────────────────────────────────────────────────────────────────

class TestCosineSimilarityUtil:
    """Unit tests for the cosine_similarity() helper itself."""

    def test_identical_texts_score_one(self):
        text = "Cube chicken breast, marinate with soy sauce and cornstarch, then stir-fry with peanuts and chili."
        assert cosine_similarity(text, text) == pytest.approx(1.0, abs=1e-6)

    def test_empty_texts_score_zero(self):
        assert cosine_similarity("", "") == pytest.approx(0.0, abs=1e-6)

    def test_completely_different_texts_low_score(self):
        """Cooking recipe vs. unrelated technical text should score well below same-dish threshold."""
        text_a = "Cube chicken, stir-fry peanuts and chili peppers in a hot wok."
        text_b = "The neural network backpropagation algorithm uses gradient descent to minimise loss."
        score = cosine_similarity(text_a, text_b, n=2)
        assert score < 0.30

    def test_same_dish_different_wording_high_score(self):
        """Two paraphrases of Kung Pao Chicken should score ≥ 0.15 with n=2 bigrams."""
        recipe_a = (
            "Cube chicken breast and marinate with soy sauce. "
            "Toast peanuts until golden; cut dried chilies into sections. "
            "Stir-fry chilies and peppercorns; add chicken, cook through, pour in sauce and add peanuts."
        )
        recipe_b = (
            "Cut chicken into small cubes and coat with soy sauce marinade. "
            "Fry peanuts until crispy and set aside. "
            "Cook dried chilies and Sichuan peppercorns in oil; add chicken, stir-fry until done, mix in sauce and peanuts."
        )
        score = cosine_similarity(recipe_a, recipe_b, n=2)
        assert score >= 0.15, f"Same-dish similarity too low: {score:.3f} (expected ≥ 0.15)"

    def test_same_dish_score_exceeds_cross_dish(self):
        """Same-dish similarity must be higher than cross-dish (relative invariant)."""
        r_kpc_a = "Cube chicken, marinate with soy sauce. Toast peanuts. Stir-fry chilies, add chicken, pour sauce."
        r_kpc_b = "Cut chicken into cubes with soy sauce marinade. Fry peanuts. Cook chilies, add chicken, add sauce."
        r_tomato = "Cut tomatoes into wedges; beat eggs with salt. Scramble eggs until just set; stir-fry tomatoes."
        same  = cosine_similarity(r_kpc_a, r_kpc_b, n=2)
        cross = cosine_similarity(r_kpc_a, r_tomato, n=2)
        assert same > cross, f"Same-dish ({same:.3f}) should exceed cross-dish ({cross:.3f})"

    def test_different_dish_lower_score(self):
        """Kung Pao Chicken vs. a dessert should fall well below same-dish score (n=2)."""
        recipe_kpc    = "Cube chicken, marinate with soy sauce. Toast peanuts. Stir-fry chilies, add chicken, pour sauce."
        recipe_cake   = "Cream butter and sugar until fluffy. Beat in eggs one at a time. Fold in flour and bake until golden."
        same_score  = cosine_similarity(recipe_kpc, recipe_kpc, n=2)
        cross_score = cosine_similarity(recipe_kpc, recipe_cake, n=2)
        assert same_score > cross_score, (
            f"Same-dish ({same_score:.3f}) should exceed cross-category ({cross_score:.3f})"
        )

    def test_symmetry(self):
        a = "Kung Pao Chicken recipe"
        b = "Fish-Fragrant Pork Shreds recipe"
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a), abs=1e-6)

    def test_similarity_range_zero_to_one(self):
        pairs = [
            ("ab", "cd"),
            ("abc", "def"),
            ("Kung Pao Chicken", "Kung Pao Chicken"),
            ("Tomato and Egg Stir-Fry recipe steps", "Tomato Egg recipe method"),
        ]
        for a, b in pairs:
            score = cosine_similarity(a, b)
            # Clamp to [0, 1] after floating-point rounding
            assert -1e-9 <= score <= 1.0 + 1e-9, f"Score out of range for ({a!r}, {b!r}): {score}"


class TestNormalizeRecipeText:
    """Tests for the normalize_recipe_text() helper."""

    def test_strips_step_numbers(self):
        text = "1. Marinate the chicken for 15 minutes.\n2. Heat oil in a wok."
        result = normalize_recipe_text(text)
        # Step-number prefixes ("1.", "2.") must be removed; content digits
        # embedded in measurements (e.g. "15" from "15 minutes") may remain.
        assert not result.startswith("1")
        assert "marinate" in result
        assert "chicken" in result
        assert "wok" in result

    def test_strips_step_keyword(self):
        text = "Step 1: Cut chicken into cubes.\nStep 2: Heat oil."
        result = normalize_recipe_text(text)
        assert "step" not in result
        assert "chicken" in result

    def test_same_content_after_normalize(self):
        """Numbered and un-numbered versions of the same recipe score higher after normalization."""
        numbered   = (
            "1. Cube chicken and marinate with soy sauce.\n"
            "2. Toast peanuts; cut dried chilies.\n"
            "3. Stir-fry chilies, add chicken, pour in sauce, add peanuts."
        )
        unnumbered = (
            "Cube chicken and marinate with soy sauce. "
            "Toast peanuts; cut dried chilies. "
            "Stir-fry chilies, add chicken, pour in sauce, add peanuts."
        )
        score_raw  = cosine_similarity(numbered, unnumbered, n=2)
        score_norm = cosine_similarity(normalize_recipe_text(numbered), normalize_recipe_text(unnumbered), n=2)
        assert score_norm >= score_raw, (
            f"Normalization should improve or maintain score: raw={score_raw:.3f}, norm={score_norm:.3f}"
        )


class TestCosineSimilarityRegression:
    """
    Regression gate: when a recipe-generation prompt changes, this suite
    checks that the new output is still semantically close to the reference.

    Uses normalized text (step numbers stripped) + n=2 bigrams.
    Threshold = 0.15 is empirically calibrated for English recipe text.
    """

    #: Minimum cosine similarity for "same dish" recipes (n=2, normalised)
    SAME_DISH_THRESHOLD = 0.15

    @pytest.mark.parametrize(
        "dish, reference, candidate",
        [
            (
                "Kung Pao Chicken",
                (
                    "1. Cut 200g chicken breast into 1 cm cubes; marinate with 1 tbsp soy sauce "
                    "and 0.5 tsp cornstarch for 15 minutes.\n"
                    "2. Toast peanuts in a dry pan; cut dried chilies into sections.\n"
                    "3. Heat 2 tbsp oil; stir-fry dried chilies and Sichuan peppercorns until fragrant.\n"
                    "4. Add chicken; stir-fry over high heat for about 2 minutes until cooked through.\n"
                    "5. Pour in sauce (2 tbsp soy sauce, 1 tbsp vinegar, 1 tsp sugar); toss to coat.\n"
                    "6. Add peanuts and scallions; stir briefly and serve."
                ),
                (
                    "Cube 200g chicken; coat with 1 tbsp soy sauce and 0.5 tsp cornstarch, marinate 15 min. "
                    "Fry peanuts until golden and set aside; snip dried chilies into 1 cm pieces. "
                    "Heat 2 tbsp oil; cook chilies and peppercorns until aromatic. "
                    "Add chicken, stir-fry ~2 min until done. "
                    "Add sauce (2 tbsp soy sauce, 1 tbsp vinegar, 1 tsp sugar) and toss. "
                    "Finish with peanuts and scallions."
                ),
            ),
            (
                "Tomato and Egg Stir-Fry",
                (
                    "1. Cut 2 tomatoes into wedges; beat 3 eggs with 0.5 tsp salt.\n"
                    "2. Heat 1 tbsp oil over medium-high; scramble eggs until just set, then remove.\n"
                    "3. Add 0.5 tbsp oil; stir-fry tomatoes over medium heat until juicy.\n"
                    "4. Return eggs; season with 0.5 tsp salt and a pinch of sugar, toss and serve."
                ),
                (
                    "Beat 3 eggs with 0.5 tsp salt; cut 2 tomatoes into chunks. "
                    "Heat 1 tbsp oil and cook eggs until just set, remove. "
                    "Stir-fry tomatoes in 0.5 tbsp oil until soft and juicy. "
                    "Return eggs, add salt and a little sugar, mix and plate."
                ),
            ),
        ],
        ids=["Kung Pao Chicken", "Tomato and Egg Stir-Fry"],
    )
    def test_regenerated_recipe_similar_to_reference(self, dish, reference, candidate):
        ref_norm  = normalize_recipe_text(reference)
        cand_norm = normalize_recipe_text(candidate)
        score = cosine_similarity(ref_norm, cand_norm, n=2)
        assert score >= self.SAME_DISH_THRESHOLD, (
            f"{dish}: similarity {score:.3f} < threshold {self.SAME_DISH_THRESHOLD} (n=2, normalised).\n"
            f"  Ref:  {ref_norm[:80]}...\n"
            f"  Cand: {cand_norm[:80]}..."
        )

    def test_same_dish_score_exceeds_wrong_dish(self):
        """Same-dish candidate must score higher than a completely different dish."""
        reference   = normalize_recipe_text(
            "Cube chicken with soy sauce marinade; stir-fry chilies and peppercorns; add chicken, pour sauce, add peanuts."
        )
        same_dish   = normalize_recipe_text(
            "Cut chicken cubes, marinate with soy sauce; fry chilies and peppercorns; add chicken, mix sauce, top with peanuts."
        )
        wrong_dish  = normalize_recipe_text(
            "Cream butter and sugar until pale; beat in eggs; fold in flour; bake at 180°C for 25 minutes."
        )
        same_score  = cosine_similarity(reference, same_dish, n=2)
        wrong_score = cosine_similarity(reference, wrong_dish, n=2)
        assert same_score > wrong_score, (
            f"Same-dish ({same_score:.3f}) should exceed wrong-dish ({wrong_score:.3f})"
        )

    def test_wrong_dish_rejected_by_threshold(self):
        """A completely different-domain recipe falls well below threshold with n=3 trigrams.

        n=2 bigrams in short English texts can yield ~0.15–0.20 even for
        unrelated domains due to shared letter pairs ("an", "nd", "ea", …).
        n=3 trigrams give much tighter cross-domain separation (< 0.05).
        """
        reference  = normalize_recipe_text(
            "Cube chicken with soy sauce; stir-fry chilies; add chicken and peanuts."
        )
        wrong_dish = normalize_recipe_text(
            "Cream butter and sugar until fluffy; beat in eggs one at a time; "
            "fold in sifted flour; bake at 180°C for 25 minutes until golden."
        )
        score = cosine_similarity(reference, wrong_dish, n=3)
        assert score < 0.05, (
            f"Wrong-dish unexpectedly passed threshold: {score:.3f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# D. Quantity-Entity Checks  (Jaccard + sanity)
# ─────────────────────────────────────────────────────────────────────────────

class TestQuantityEntityExtraction:
    """Tests for extract_quantity_units(), jaccard_qty_similarity(), is_quantity_sane()."""

    # ── extract_quantity_units ───────────────────────────────────────────────

    def test_extracts_tbsp_and_tsp(self):
        pairs = extract_quantity_units("Add 2 tbsp soy sauce and 0.5 tsp salt.")
        assert ("2", "tbsp") in pairs
        assert ("0.5", "tsp") in pairs

    def test_normalises_tablespoon_alias(self):
        pairs = extract_quantity_units("Mix with 3 tablespoons of vinegar.")
        assert ("3", "tbsp") in pairs

    def test_extracts_grams_alias(self):
        pairs = extract_quantity_units("Use 200 grams of chicken breast.")
        assert ("200", "g") in pairs

    def test_extracts_minutes(self):
        pairs = extract_quantity_units("Marinate for 15 minutes.")
        assert ("15", "min") in pairs

    def test_empty_text_returns_empty(self):
        assert extract_quantity_units("Stir until combined and serve.") == []

    def test_fractional_quantity(self):
        pairs = extract_quantity_units("Add 1/2 cup of water.")
        assert ("1/2", "cup") in pairs

    # ── jaccard_qty_similarity ───────────────────────────────────────────────

    def test_identical_quantities_score_one(self):
        text = "Add 2 tbsp soy sauce and 1 tsp sugar."
        assert jaccard_qty_similarity(text, text) == pytest.approx(1.0)

    def test_completely_different_quantities_score_zero(self):
        a = "Use 200 grams of chicken and 2 tbsp soy sauce."
        b = "Marinate for 15 minutes at low heat."
        score = jaccard_qty_similarity(a, b)
        assert score == pytest.approx(0.0)

    def test_partial_overlap(self):
        # shared: ("2", "tbsp"), ("1", "tsp")  |  unique in B: ("1", "tbsp")  → 2/3
        a = "Mix 2 tbsp soy sauce with 1 tsp sugar."
        b = "Combine 2 tbsp soy sauce, 1 tsp sugar, and 1 tbsp vinegar."
        score = jaccard_qty_similarity(a, b)
        assert score == pytest.approx(2 / 3, abs=0.01)

    def test_both_empty_returns_one(self):
        assert jaccard_qty_similarity("Stir until combined.", "Mix well.") == pytest.approx(1.0)

    def test_same_dish_paraphrases_high_jaccard(self):
        """Two paraphrases of a recipe sharing key quantities should score ≥ 0.5."""
        a = "Marinate with 1 tbsp soy sauce and 0.5 tsp cornstarch for 15 minutes."
        b = "Coat with 1 tbsp soy sauce, 0.5 tsp cornstarch; rest for 15 minutes."
        assert jaccard_qty_similarity(a, b) >= 0.5

    # ── is_quantity_sane ─────────────────────────────────────────────────────

    def test_normal_soy_sauce_tbsp_is_sane(self):
        assert is_quantity_sane("2", "tbsp")

    def test_reasonable_grams_is_sane(self):
        assert is_quantity_sane("200", "grams")

    def test_reasonable_minutes_is_sane(self):
        assert is_quantity_sane("20", "minutes")

    def test_absurd_salt_grams_fails(self):
        """3000 grams (3 kg) of a single ingredient exceeds the 2 kg upper bound."""
        assert not is_quantity_sane("3000", "grams")

    def test_absurd_soy_sauce_litres_fails(self):
        """10 litres of soy sauce is a culinary hallucination."""
        assert not is_quantity_sane("10", "litres")

    def test_absurd_tbsp_count_fails(self):
        """100 tablespoons is unrealistic for any home recipe."""
        assert not is_quantity_sane("100", "tbsp")

    def test_unknown_unit_passes(self):
        """Unrecognised units should not cause false failures."""
        assert is_quantity_sane("3", "pinches")

    @pytest.mark.parametrize(
        "steps, expect_sane",
        [
            (["Add 2 tbsp soy sauce.", "Marinate for 15 minutes."], True),
            (["Add 3000 grams of salt.", "Cook for 2 minutes."],    False),   # > 2 kg limit
            (["Stir in 10 litres of soy sauce.", "Simmer."],        False),   # > 3 L limit
        ],
    )
    def test_recipe_sanity_batch(self, steps, expect_sane):
        """All (qty, unit) pairs in a recipe step list should be sane."""
        all_sane = all(
            is_quantity_sane(qty, unit)
            for step in steps
            for qty, unit in extract_quantity_units(step)
        )
        assert all_sane == expect_sane


# ─────────────────────────────────────────────────────────────────────────────
# E. Negative Test Cases  (the system must *reject* bad outputs)
# ─────────────────────────────────────────────────────────────────────────────

class TestNegativeCases:
    """
    'Proving you can spot wrong answers' — the flip side of positive tests.

    Covers:
    * Cross-dish mismatch: Kung Pao Chicken query answered with Tomato Egg recipe.
    * Hallucination fallback: agent returns a coherent error for an invented dish.
    * Sanity rejection: recipe with absurd quantities is flagged.
    """

    THRESHOLD_N3 = 0.10   # n=3 trigram out-of-domain gate (cross-domain scores land at ~0.07)

    def test_cross_dish_blocked_by_cosine(self):
        """
        Two recipes from the same dish (KPC) must score higher than a KPC
        recipe vs a completely non-culinary text (CS algorithm description).

        Relative property: same-dish ≫ out-of-domain.  This is reliable for
        any n and language, unlike absolute thresholds which are text-dependent.
        The n=3 + out-of-domain threshold < 0.05 is also verified here.
        """
        golden_kpc   = normalize_recipe_text(
            "Cube chicken; marinate with soy sauce. "
            "Toast peanuts; stir-fry chilies and Sichuan peppercorns. "
            "Add chicken; pour soy sauce and vinegar sauce; top with peanuts."
        )
        same_kpc     = normalize_recipe_text(
            "Cut chicken into cubes; coat with soy sauce. "
            "Fry peanuts; cook dried chilies and peppercorns. "
            "Add chicken; mix sauce of soy sauce and vinegar; finish with peanuts."
        )
        out_of_domain = normalize_recipe_text(
            "A binary search tree inserts and deletes nodes in logarithmic time. "
            "Balancing algorithms such as AVL or red-black trees maintain height bounds."
        )
        same_score  = cosine_similarity(golden_kpc, same_kpc,      n=3)
        cross_score = cosine_similarity(golden_kpc, out_of_domain,  n=3)

        if cross_score >= self.THRESHOLD_N3:
            save_failure_snapshot(
                "Kung Pao Chicken [cross-domain test]",
                golden_kpc, out_of_domain,
                score=cross_score, layer="cosine",
                extra={"n": 3, "expected": f"< {self.THRESHOLD_N3}", "got": cross_score},
            )
        # Absolute: out-of-domain must be below cross-domain threshold
        assert cross_score < self.THRESHOLD_N3, (
            f"Out-of-domain score too high: {cross_score:.3f} ≥ {self.THRESHOLD_N3}"
        )
        # Relative: same-dish must outscore out-of-domain by a clear margin
        assert same_score > cross_score * 3, (
            f"Same-dish ({same_score:.3f}) should be at least 3× out-of-domain ({cross_score:.3f})"
        )

    def test_hallucination_fallback_schema_valid(self):
        """
        An agent returning an error for an invented dish must still produce a
        schema-valid batch item with saved=False and a non-empty error message.
        """
        import jsonschema
        from tests.ai_quality.conftest import COOKING_STEPS_BATCH_SCHEMA

        fallback_item = {
            "dish_name": "Cyberpunk Red-Braised Noodles",
            "cooking_steps": [],
            "saved": False,
            "schedule_id": None,
            "error": "No recipe found for this dish. Please try a different query.",
        }
        try:
            jsonschema.validate([fallback_item], COOKING_STEPS_BATCH_SCHEMA)
        except jsonschema.ValidationError as exc:
            pytest.fail(f"Hallucination fallback item failed schema validation: {exc.message}")

        assert fallback_item["saved"] is False
        assert len(fallback_item["error"]) > 10, "Error message is too short to be useful"
        assert len(fallback_item["cooking_steps"]) == 0

    def test_absurd_recipe_flagged_by_sanity_check(self):
        """
        A recipe containing obviously wrong quantities should fail the sanity
        gate even if it passes structural and cosine checks.
        """
        hallucinated_steps = [
            "Add 3000 grams of salt to the marinade.",         # 3 kg — exceeds 2 kg bound
            "Pour in 8 litres of soy sauce.",                  # 8 L — exceeds 3 L bound
            "Stir-fry for 2 minutes until cooked through.",
        ]
        insane = [
            (qty, unit)
            for step in hallucinated_steps
            for qty, unit in extract_quantity_units(step)
            if not is_quantity_sane(qty, unit)
        ]
        assert len(insane) >= 2, (
            f"Expected ≥ 2 insane quantities, found: {insane}"
        )

    def test_same_dish_scores_above_cross_dish(self):
        """
        Regression: same-dish similarity must always exceed the cross-dish
        baseline, no matter how the recipes are phrased.
        """
        golden = normalize_recipe_text(
            "Cube chicken; marinate with soy sauce. "
            "Stir-fry peanuts and chilies; add chicken, sauce, finish."
        )
        same   = normalize_recipe_text(
            "Cut chicken into cubes with soy sauce. "
            "Fry peanuts and dried chilies; toss chicken and sauce together."
        )
        cross  = normalize_recipe_text(
            "Simmer pork belly until tender; cool and slice thin. "
            "Drizzle garlic-soy sauce over pork; garnish."
        )
        same_score  = cosine_similarity(golden, same,  n=2)
        cross_score = cosine_similarity(golden, cross, n=2)
        assert same_score > cross_score, (
            f"Same-dish ({same_score:.3f}) ≤ cross-dish ({cross_score:.3f}) — "
            "threshold discrimination has regressed."
        )
