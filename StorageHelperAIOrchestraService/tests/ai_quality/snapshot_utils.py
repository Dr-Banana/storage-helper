"""
Failure Snapshot Utilities
===========================
When a semantic / cosine-similarity test fails, calling ``save_failure_snapshot``
writes a side-by-side diff to ``tests/failure_snapshots/``.

Layout of each snapshot file (JSON)::

    {
      "timestamp": "20260306_213000",
      "dish": "Kung Pao Chicken",
      "score": 0.08,              # cosine similarity, if applicable
      "verdict": "FAIL",          # LLM judge verdict, if applicable
      "layer": "cosine",          # "cosine" | "llm_judge"
      "diff": {
        "golden":    "...",
        "candidate": "..."
      },
      "side_by_side": [
        ["Golden line 1", "Candidate line 1"],
        ...
      ],
      "extra": { ... }            # any additional debug info
    }

Usage::

    from tests.ai_quality.snapshot_utils import save_failure_snapshot

    if score < threshold:
        save_failure_snapshot(dish, golden, candidate, score=score, layer="cosine")
    assert score >= threshold, ...
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Snapshots land next to the test files for easy discovery.
_SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "failure_snapshots")


def _side_by_side(golden: str, candidate: str) -> List[Tuple[str, str]]:
    """
    Return a list of (golden_line, candidate_line) pairs for a visual diff.
    Shorter side is padded with empty strings.
    """
    g_lines = golden.splitlines()
    c_lines = candidate.splitlines()
    length = max(len(g_lines), len(c_lines))
    g_lines += [""] * (length - len(g_lines))
    c_lines += [""] * (length - len(c_lines))
    return list(zip(g_lines, c_lines))


def save_failure_snapshot(
    dish: str,
    golden: str,
    candidate: str,
    score: Optional[float] = None,
    verdict: Optional[str] = None,
    layer: str = "cosine",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Persist a failure snapshot and return the absolute path of the created file.

    Parameters
    ----------
    dish:       Dish name (used in the filename).
    golden:     The approved reference text.
    candidate:  The AI-generated text under test.
    score:      Cosine similarity score (float), if available.
    verdict:    LLM judge verdict string ("PASS"/"FAIL"), if available.
    layer:      Which layer triggered the failure: "cosine" or "llm_judge".
    extra:      Any additional diagnostic dict to embed in the snapshot.
    """
    os.makedirs(_SNAPSHOT_DIR, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_dish = dish.replace(" ", "_").replace("/", "-")[:40]
    filename = f"{ts}_{safe_dish}.json"
    path = os.path.join(_SNAPSHOT_DIR, filename)

    data: Dict[str, Any] = {
        "timestamp": ts,
        "dish": dish,
        "score": round(score, 4) if score is not None else None,
        "verdict": verdict,
        "layer": layer,
        "diff": {
            "golden":    golden,
            "candidate": candidate,
        },
        "side_by_side": _side_by_side(golden, candidate),
    }
    if extra:
        data["extra"] = extra

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    return path
