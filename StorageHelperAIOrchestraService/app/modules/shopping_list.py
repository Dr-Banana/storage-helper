"""
Shopping-list aggregation — turn a set of planned dishes into a deduped
ingredient list ready for pricing.

Pure logic, no network. Quantities across dishes are collected (not summed),
because units are inconsistent ("2 cloves" + "1 tbsp" can't be added
meaningfully); we group by normalized ingredient name and keep the raw
quantities plus which dishes need them.
"""
import re
from typing import Any, Dict, List


def _normalize(name: str) -> str:
    n = (name or "").strip().lower()
    n = re.sub(r"\s+", " ", n)
    return n


def aggregate_ingredients(dishes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    dishes: [{"name": str, "ingredients": [{"name": str, "quantity": str}, ...]}, ...]
    returns: [{"ingredient": display_name,
               "quantities": [str, ...],
               "dishes": [dish_name, ...]}, ...]
    sorted by ingredient name.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for dish in dishes or []:
        dish_name = dish.get("name", "")
        for ing in dish.get("ingredients", []) or []:
            raw_name = ing.get("name", "") if isinstance(ing, dict) else str(ing)
            if not raw_name:
                continue
            key = _normalize(raw_name)
            if not key:
                continue
            g = groups.setdefault(key, {"ingredient": raw_name.strip(), "quantities": [], "dishes": []})
            qty = ing.get("quantity", "") if isinstance(ing, dict) else ""
            if qty:
                g["quantities"].append(qty)
            if dish_name and dish_name not in g["dishes"]:
                g["dishes"].append(dish_name)
    return [groups[k] for k in sorted(groups.keys())]


def summarize_priced(priced_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    priced_items: [{"ingredient": str, "price": float|None, "matched_name": str,
                    "mock": bool, ...}, ...]
    Returns totals + counts of matched/unmatched.
    """
    total = 0.0
    matched = 0
    unmatched = []
    any_mock = False
    for it in priced_items:
        price = it.get("price")
        if price is not None:
            total += price
            matched += 1
        else:
            unmatched.append(it.get("ingredient", ""))
        if it.get("mock"):
            any_mock = True
    return {
        "total": round(total, 2),
        "matched_count": matched,
        "unmatched": unmatched,
        "item_count": len(priced_items),
        "mock": any_mock,
    }
