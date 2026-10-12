"""Application identity, authorization, and effective-scope primitives."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
import re

import pandas as pd

Persona = Literal["Planner", "Brand Manager", "Store Manager", "Leadership"]

# One reusable presentation policy per role. Authorization remains enforced by
# the ID sets on UserContext; these policies only choose relevant wording and
# prioritization for the already-authorized result.
ROLE_POLICIES = {
    "Planner": {
        "focus": "replenishment, supplier lead time, demand and stockout risk",
        "priority": ("low-stock", "stockout", "reorder", "store", "demand", "coverage"),
    },
    "Store Manager": {
        "focus": "store availability, reorder attention and local stockout risk",
        "priority": ("store", "low-stock", "inventory", "reorder", "stockout"),
    },
    "Brand Manager": {
        "focus": "authorized brand sales, slow-moving products and comparisons",
        "priority": ("sales", "slow-moving", "brand", "inventory"),
    },
    "Leadership": {
        "focus": "executive inventory value, risk, category and brand summaries",
        "priority": ("risk", "value", "category", "exposure"),
    },
}


def role_policy(persona: str) -> dict[str, object]:
    return dict(ROLE_POLICIES.get(persona, ROLE_POLICIES["Planner"]))


@dataclass(frozen=True)
class UserContext:
    user_id: str = "demo-sai"
    display_name: str = "Sai"
    persona: Persona = "Planner"
    authorized_store_ids: tuple[int, ...] | None = None
    authorized_product_ids: tuple[int, ...] | None = None
    authorized_brand_ids: tuple[int, ...] | None = None
    authorized_region_ids: tuple[int, ...] | None = None
    # Human-readable labels are server-derived and used only for safe
    # clarification/suggestion wording; IDs remain the enforcement boundary.
    authorized_brands: tuple[str, ...] | None = None
    authorized_regions: tuple[str, ...] | None = None
    preferences: dict[str, str] = field(default_factory=dict)


def effective_ids(requested: list[int] | None, authorized: tuple[int, ...] | None) -> tuple[list[int] | None, bool]:
    """Return the authorized intersection and whether a request was restricted."""
    if authorized is None:
        return requested, False
    allowed = set(authorized)
    if requested is None:
        return sorted(allowed), False
    result = sorted(set(requested) & allowed)
    return result, set(requested) != set(result)


def persona_followups(persona: str, response_type: str, has_store: bool = False) -> list[str]:
    choices = {
        "Planner": ["Show stockout risk", "Show products below reorder level", "What if demand increases 20%?"],
        "Brand Manager": ["Compare authorized brands", "Show authorized slow-moving products", "Show authorized brand sales trend"],
        "Store Manager": ["Show low-stock products in my region", "Show authorized stores", "Show stockout risk in my region"],
        "Leadership": ["Show top inventory risks", "Compare categories", "Show highest inventory value"],
    }
    followups = list(choices.get(persona, choices["Planner"]))
    if response_type == "stockout_risk":
        followups = {
            "Planner": ["Show products below reorder level", "Compare DOI with lead time", "What if demand increases 20%?"],
            "Brand Manager": ["Show sales for authorized products", "Compare authorized brands", "Show authorized slow-moving items"],
            "Store Manager": ["Show these products by authorized store", "Show low-stock items in my region", "Show authorized store sales"],
            "Leadership": ["Show total inventory exposure", "Compare affected categories", "Show biggest inventory risks"],
        }.get(persona, followups)
    elif response_type == "inventory_health":
        followups = {
            "Planner": ["Show stockout risk", "Show products below reorder level", "What if demand increases 20%?"],
            "Brand Manager": ["Show authorized slow-moving products", "Compare authorized brands", "Show authorized brand sales trend"],
            "Store Manager": ["Show low-stock products in my region", "Show stockout risk in my region", "Show authorized stores"],
            "Leadership": ["Show top inventory risks", "Compare categories", "Show highest inventory value"],
        }.get(persona, followups)
    if response_type == "ranking":
        followups.insert(0, "Which one has the highest sales?")
    if has_store and "Show this by store" in followups:
        followups.remove("Show this by store")
    return followups[:3]


def build_followups(
    *,
    question: str,
    intent,
    dataframe: pd.DataFrame,
    response_type: str,
    persona: str,
    authorized_scope: UserContext | None = None,
) -> list[str]:
    """Select executable, evidence-backed next questions for one response.

    Labels are taken only from the deterministic result rows or current filters;
    this function never invents product, brand, or location names.
    """
    text = re.sub(r"\s+", " ", question.casefold()).strip()
    candidates: list[str] = []
    frame = dataframe if isinstance(dataframe, pd.DataFrame) else pd.DataFrame()

    def values(column: str) -> list[str]:
        if column not in frame.columns:
            return []
        return [str(value) for value in frame[column].dropna().unique().tolist() if str(value).strip()]

    products = values("product_name")
    cities = values("city")
    stores = values("store_name")
    brands = values("brand")
    authorized_brands = tuple(getattr(authorized_scope, "authorized_brands", ()) or ()) if authorized_scope else ()
    if authorized_brands:
        brands = [value for value in brands if value in authorized_brands] or list(authorized_brands)
    filters = getattr(intent, "filters", None)
    if filters:
        brands = list(dict.fromkeys(brands + [str(v) for v in getattr(filters, "brands", [])]))
        products = list(dict.fromkeys(products + [str(v) for v in getattr(filters, "products", [])]))

    product = products[0] if len(products) == 1 else None
    brand = brands[0] if len(brands) == 1 else None
    city = cities[0] if len(cities) == 1 else None
    time = getattr(intent, "time", None)
    time_kind = getattr(time, "kind", "none") if time else "none"
    time_suffix = ""
    if time_kind == "month" and getattr(time, "month", None) and getattr(time, "year", None):
        import calendar
        time_suffix = f" in {calendar.month_name[int(time.month)]} {int(time.year)}"
    elif time_kind == "latest":
        time_suffix = " (latest)"

    def scope_suffix(*, include_product: bool = False) -> str:
        if include_product and product:
            scope = f" for {product}"
        elif city:
            scope = f" in {city}"
        elif stores and len(stores) == 1:
            scope = f" at {stores[0]}"
        else:
            scope = ""
        return scope + time_suffix

    if response_type == "inventory_visibility":
        if product:
            candidates.append(f"Show {product} inventory by store")
        elif city:
            candidates.append(f"Show inventory by store in {city}")
        elif stores:
            candidates.append(f"Show inventory by store at {stores[0]}")
        else:
            candidates.append("Show inventory by store")
        candidates.append(f"Show stockout risk{scope_suffix(include_product=True)}")
        candidates.append(f"Show products below reorder level{scope_suffix()}")
    elif response_type == "stockout_risk":
        if stores or city:
            location = city or stores[0]
            candidates.append(f"Show low-stock products in {location}")
        else:
            candidates.append("Show low-stock products")
        if product:
            candidates.append(f"Show {product} inventory by store")
        elif stores or city:
            candidates.append("Which store has the most high-risk products?")
        candidates.append("What happens to these products if demand increases 20%?")
    elif response_type == "inventory_health":
        candidates.append(f"Show stockout risk{scope_suffix(include_product=True)}")
        if city:
            candidates.append(f"Show low-stock products in {city}")
        elif stores:
            candidates.append(f"Show low-stock products in {stores[0]}")
        else:
            candidates.append("Show products below reorder level")
        candidates.append(f"What happens to these products if demand increases 20%?{time_suffix}")
    elif response_type in {"sales", "comparison"}:
        if brand:
            candidates.extend([f"Show {brand} sales trend", f"Which {brand} products are slow-moving?"])
        elif len(brands) >= 2:
            candidates.append(f"Compare {brands[0]} and {brands[1]} sales")
            candidates.append(f"Show {brands[0]} sales trend")
        elif product:
            candidates.extend([f"Show {product} inventory by store", f"What happens if {product} demand increases 20%?"])
    elif response_type in {"scenario", "what_if"}:
        if product:
            candidates.extend([
                f"Show {product} inventory by store",
                f"Show {product} sales trend{time_suffix}",
            ])
        elif city:
            candidates.append(f"Show inventory by store in {city}")
        else:
            candidates.append("Show inventory by store")
        candidates.append("Show stockout risk")
    elif response_type == "ranking":
        if "sales" not in text:
            candidates.append("Which one has the highest sales?")
        if "stockout" not in text and "risk" not in text:
            candidates.append("What about stockout risk?")
        if not stores and not city:
            candidates.append("Show this by store")
    elif response_type == "explanation":
        if product:
            candidates.extend([f"Show {product} inventory by store", f"What happens if {product} demand increases 20%?"])
    elif response_type == "historical_analysis":
        candidates.extend(["Compare the inventory trend by category", "Show latest inventory value by category"])
    elif response_type == "executive_summary":
        candidates.extend(["Rank categories by inventory value", "Show store-wise sales revenue", "Show high stockout risk positions"])
    elif response_type == "comparison":
        if getattr(intent, "group_by", None) in {"category", "brand", "store", "city"}:
            candidates.extend([f"Rank {intent.group_by}s by inventory value", f"Rank {intent.group_by}s by sales revenue"])

    # Persona changes ordering, not the evidence or the allowed scope.
    priority = list(role_policy(persona).get("priority", ()))
    candidates.sort(key=lambda item: min((next((i for i, token in enumerate(priority) if token in item.casefold()), len(priority))), len(priority)))
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]+", " ", candidate.casefold()).strip()
        if not key or key in seen or key in re.sub(r"[^a-z0-9]+", " ", text).strip():
            continue
        seen.add(key)
        result.append(candidate)
    return result[:3]
