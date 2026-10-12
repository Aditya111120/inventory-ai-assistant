"""Entity resolver: turns the names a user typed into real product and store IDs.

Deterministic code, not the LLM. It also produces the plain-English lines the Trust Panel
shows ("'Bangalore' -> Bengaluru (2 stores)") and the 'did you mean...?' questions.

Semantics: within one filter field the values are OR-ed (Samsung OR LG); across fields they
are AND-ed (brand AND category). Product-level fields (brands, categories, products) restrict
product_ids; store-level fields (stores, cities, regions) restrict store_ids. None means
"no restriction", an empty list means "nothing matches".
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from app.llm.schema import Filters

AUTO_CUTOFF = 0.88
SUGGEST_CUTOFF = 0.60
MAX_SUGGESTIONS = 3
UNCLASSIFIED_SUBCATEGORY_TERMS = {
    "unclassified", "uncategorized", "no subcategory", "without subcategory",
}

BRAND_ALIASES = {"mi": "Xiaomi", "redmi": "Xiaomi"}
CATEGORY_ALIASES = {
    "tv": "Televisions",
    "tvs": "Televisions",
    "television": "Televisions",
    "smart tv": "Televisions",
    "phone": "Smartphones",
    "phones": "Smartphones",
    "mobile": "Smartphones",
    "mobiles": "Smartphones",
    "mobile phone": "Smartphones",
    "mobile phones": "Smartphones",
    "smartphone": "Smartphones",
    "notebook": "Laptops",
    "notebooks": "Laptops",
    "laptop": "Laptops",
    "tablet": "Tablets",
    "headphone": "Audio",
    "headphones": "Audio",
    "earbuds": "Audio",
    "earphones": "Audio",
    "speaker": "Audio",
    "speakers": "Audio",
    "soundbar": "Audio",
    "camera": "Cameras",
    "appliance": "Appliances",
    "home appliances": "Appliances",
}
CITY_ALIASES = {
    "bangalore": "Bengaluru",
    "bombay": "Mumbai",
    "new delhi": "Delhi",
    "madras": "Chennai",
    "calcutta": "Kolkata",
    "poona": "Pune",
}

_PRODUCT_LEVEL = ("brands", "categories", "subcategories", "products")
_STORE_LEVEL = ("stores", "cities", "regions")
_SINGULAR = {
    "brands": "brand",
    "categories": "category",
    "subcategories": "subcategory",
    "products": "product",
    "stores": "store",
    "cities": "city",
    "regions": "region",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).casefold()).strip()


def _variants(n: str) -> list[str]:
    out = [n, n + "s"]
    if n.endswith("s"):
        out.append(n[:-1])
    if n.endswith("region"):
        out.append(n[: -len("region")].strip())
    if n.endswith(" india"):
        out.append(n[: -len(" india")].strip())
    return out


@dataclass
class Match:
    kind: str
    term: str
    label: str
    how: str
    count: int
    found_as: str


@dataclass
class Unresolved:
    kind: str
    term: str
    suggestions: list[str]


@dataclass
class Resolution:
    product_ids: Optional[list[int]] = None
    store_ids: Optional[list[int]] = None
    matches: list[Match] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    empty_reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.unresolved

    @property
    def empty(self) -> bool:
        return self.product_ids == [] or self.store_ids == []

    def explain(self) -> list[str]:
        lines = []
        for m in self.matches:
            unit = "store" if m.found_as in _STORE_LEVEL else "product"
            unit = unit if m.count == 1 else unit + "s"
            how = "" if m.how == "exact" else f" [{m.how} match]"
            moved = (
                ""
                if m.found_as == m.kind
                else f" (given as {_SINGULAR[m.kind]}, found as {_SINGULAR[m.found_as]})"
            )
            lines.append(f"'{m.term}' -> {m.label}{how}{moved}: {m.count} {unit}")
        for u in self.unresolved:
            hint = f" Did you mean {', '.join(u.suggestions)}?" if u.suggestions else ""
            lines.append(f"Could not match {_SINGULAR[u.kind]} '{u.term}'.{hint}")
        if self.empty_reason:
            lines.append(self.empty_reason)
        return lines

    def clarification_question(self) -> str:
        parts = []
        for u in self.unresolved:
            if u.suggestions:
                parts.append(
                    f"I couldn't find {_SINGULAR[u.kind]} '{u.term}'. Did you mean "
                    f"{' or '.join(u.suggestions)}?"
                )
            else:
                parts.append(
                    f"I couldn't find any {_SINGULAR[u.kind]} called '{u.term}' in the catalog."
                )
        return " ".join(parts)


class _Pool:
    """Name -> id-set lookup for one field."""

    def __init__(
        self,
        labels_to_ids: dict[str, set[int]],
        aliases: Optional[dict[str, str]] = None,
        partial: bool = False,
        fuzzy: bool = True,
    ):
        self.ids = labels_to_ids
        self.index = {_norm(label): label for label in labels_to_ids}
        self.aliases = {
            k: v for k, v in (aliases or {}).items() if _norm(v) in self.index
        }
        self.partial = partial
        self.fuzzy = fuzzy

    def lookup(self, term: str):
        n = _norm(term)
        if not n:
            return None, []
        for v in _variants(n):
            if v in self.index:
                return (self.index[v], "exact"), []
            if v in self.aliases:
                return (self.aliases[v], "alias"), []
        if self.partial:
            toks = set(n.split())
            hits = [lab for nl, lab in self.index.items() if toks and toks <= set(nl.split())]
            if len(hits) == 1:
                return (hits[0], "partial"), []
        # Geographic names are high-impact filters.  A typo may be offered as
        # a suggestion, but must never silently become a different store/city
        # (for example, treating a country name as a nearby store).
        if not self.fuzzy:
            return None, []
        close = difflib.get_close_matches(
            n, list(self.index), n=MAX_SUGGESTIONS, cutoff=SUGGEST_CUTOFF
        )
        if close:
            ratios = [difflib.SequenceMatcher(None, n, c).ratio() for c in close]
            if ratios[0] >= AUTO_CUTOFF and (
                len(ratios) == 1 or ratios[0] - ratios[1] > 0.05
            ):
                return (self.index[close[0]], "fuzzy"), []
            return None, [self.index[c] for c in close]
        return None, []

    def ids_for(self, label: str) -> set[int]:
        return self.ids[label]


class _ProductPool:
    """Products can be named by product name, model code or SKU, or described by words."""

    def __init__(self, products: pd.DataFrame):
        self.by_key: dict[str, set[int]] = {}
        self.labels: dict[int, str] = {}
        self.tokens: dict[int, set[str]] = {}
        for r in products.itertuples():
            self.labels[r.product_id] = r.product_name
            self.tokens[r.product_id] = set(_norm(r.product_name).split())
            for key in (r.product_name, r.model, r.sku):
                self.by_key.setdefault(_norm(key), set()).add(r.product_id)
        self.names = {_norm(v): v for v in self.labels.values()}

    def lookup(self, term: str):
        n = _norm(term)
        if not n:
            return None, []
        if n in self.by_key:
            ids = self.by_key[n]
            return (
                (sorted(self.labels[i] for i in ids)[0] if len(ids) == 1 else term),
                "exact",
                ids,
            ), []
        toks = set(n.split())
        ids = {i for i, t in self.tokens.items() if toks <= t}
        if ids:
            return (term, "partial", ids), []
        keys = list(self.by_key)
        close = difflib.get_close_matches(
            n, keys, n=MAX_SUGGESTIONS, cutoff=SUGGEST_CUTOFF
        )
        if close:
            ratios = [difflib.SequenceMatcher(None, n, c).ratio() for c in close]
            if ratios[0] >= AUTO_CUTOFF and (
                len(ratios) == 1 or ratios[0] - ratios[1] > 0.05
            ):
                ids = self.by_key[close[0]]
                return (self.labels[sorted(ids)[0]], "fuzzy", ids), []
            return None, [self.labels[sorted(self.by_key[c])[0]] for c in close]
        return None, []


class EntityResolver:
    def __init__(self, products: pd.DataFrame, stores: pd.DataFrame):
        self.products = products.copy()
        self.stores = stores.copy()
        def group(df: pd.DataFrame, col: str, idcol: str) -> dict[str, set[int]]:
            if col not in df.columns:
                return {}
            df = df[df[col].notna()]
            return {
                str(k): set(map(int, v))
                for k, v in df.groupby(col)[idcol].apply(list).items()
            }

        self.pools = {
            "brands": _Pool(group(products, "brand", "product_id"), BRAND_ALIASES),
            "categories": _Pool(
                group(products, "category", "product_id"), CATEGORY_ALIASES
            ),
            "subcategories": _Pool(group(products, "subcategory", "product_id")),
            "stores": _Pool(group(stores, "store_name", "store_id"), partial=True, fuzzy=False),
            "cities": _Pool(group(stores, "city", "store_id"), CITY_ALIASES, fuzzy=False),
            "regions": _Pool(group(stores, "region", "store_id"), fuzzy=False),
        }
        self.product_pool = _ProductPool(products)
        self.all_product_ids = set(map(int, products["product_id"]))
        self.all_store_ids = set(map(int, stores["store_id"]))

    def catalog_brand_mentions(self, text: str) -> list[str]:
        """Return catalog-backed brand labels explicitly mentioned in text.

        This is a validation aid for brand comparisons, not a general-purpose
        question parser. It only returns exact catalog label mentions.
        """
        normalized = f" {_norm(text)} "
        found = []
        for label in self.pools["brands"].ids:
            candidate = _norm(label)
            if candidate and f" {candidate} " in normalized:
                found.append(str(label))
        return sorted(found, key=lambda value: _norm(value))

    def explicit_filter_mentions(self, text: str) -> Filters:
        """Extract exact catalog entity mentions without relying on the LLM parse."""
        normalized = f" {_norm(text)} "

        def mentioned(pool: _Pool) -> list[str]:
            labels = set()
            for label in pool.ids:
                candidate = _norm(label)
                if candidate and f" {candidate} " in normalized:
                    labels.add(label)
            for alias, label in pool.aliases.items():
                candidate = _norm(alias)
                if candidate and f" {candidate} " in normalized:
                    labels.add(label)
            return sorted(labels, key=_norm)

        product_terms = set()
        for key, product_ids in self.product_pool.by_key.items():
            if len(key.split()) < 2 and len(key) < 4:
                continue
            if f" {key} " in normalized:
                product_terms.add(key)

        product_labels = []
        for term in sorted(product_terms, key=lambda value: (len(value), value)):
            match, _ = self.product_pool.lookup(term)
            if match:
                product_labels.append(match[0])

        return Filters(
            products=list(dict.fromkeys(product_labels)),
            brands=mentioned(self.pools["brands"]),
            categories=mentioned(self.pools["categories"]),
            subcategories=mentioned(self.pools["subcategories"]),
            stores=mentioned(self.pools["stores"]),
            cities=mentioned(self.pools["cities"]),
            regions=mentioned(self.pools["regions"]),
        )

    def store_options(self, authorized_store_ids=None) -> list[dict[str, object]]:
        """List store labels only inside the supplied authorization scope."""
        frame = self.stores.copy()
        if authorized_store_ids is not None:
            allowed = {int(value) for value in authorized_store_ids}
            frame = frame[frame["store_id"].astype(int).isin(allowed)]
        frame = frame.sort_values(["city", "store_name", "store_id"])
        return [
            {"store_id": int(row.store_id), "store_name": str(row.store_name), "city": str(row.city)}
            for row in frame.itertuples()
        ]

    def city_store_options(
        self,
        city: str,
        authorized_store_ids: Optional[tuple[int, ...] | list[int] | set[int]] = None,
    ) -> list[dict[str, object]]:
        """Return concrete, authorized stores for a city.

        This is intentionally a resolver/catalog operation, not a SQL query.
        Callers receive display labels and IDs only for stores in the supplied
        authorization scope; the orchestrator replaces IDs with opaque choice
        tokens before returning them to clients.
        """
        hit, _ = self.pools["cities"].lookup(city)
        if not hit:
            return []
        label, _how = hit
        frame = self.stores[self.stores["city"].astype(str) == str(label)].copy()
        if authorized_store_ids is not None:
            allowed = {int(v) for v in authorized_store_ids}
            frame = frame[frame["store_id"].astype(int).isin(allowed)]
        frame = frame.sort_values(["store_name", "store_id"])
        return [
            {
                "store_id": int(row.store_id),
                "store_name": str(row.store_name),
                "city": str(row.city),
                "region": str(row.region) if pd.notna(row.region) else None,
            }
            for row in frame.itertuples()
        ]

    def location_options(
        self,
        authorized_store_ids: Optional[tuple[int, ...] | list[int] | set[int]] = None,
    ) -> list[dict[str, object]]:
        """Return authorized city labels for a missing-location clarification."""
        frame = self.stores.copy()
        if authorized_store_ids is not None:
            allowed = {int(v) for v in authorized_store_ids}
            frame = frame[frame["store_id"].astype(int).isin(allowed)]
        frame = frame.dropna(subset=["city"]).sort_values("city")
        return [
            {"city": str(city), "store_count": int(group["store_id"].nunique())}
            for city, group in frame.groupby("city", sort=True)
        ]

    def _find(self, kind: str, term: str, level: tuple[str, ...]):
        """Try the field the term was given in first, then the other fields at that level."""
        # A natural-language subtype such as "soundbar" may be emitted by the
        # intent model as a category. Prefer an actual assigned subcategory
        # over the broad Audio alias so filtering remains evidence-based.
        if _norm(term) in UNCLASSIFIED_SUBCATEGORY_TERMS and "subcategory" in self.products.columns:
            ids = set(self.products.loc[self.products["subcategory"].isna(), "product_id"].astype(int))
            return Match(kind, term, "Unclassified products", "exact", len(ids), "subcategories"), ids, []
        if kind in ("categories", "subcategories") and "subcategories" in self.pools:
            hit, sug = self.pools["subcategories"].lookup(term)
            if hit:
                label, how = hit
                ids = self.pools["subcategories"].ids_for(label)
                return Match(kind, term, label, how, len(ids), "subcategories"), set(ids), []
        order = [kind] + [k for k in level if k != kind]
        suggestions: list[str] = []
        for k in order:
            if k == "products":
                hit, sug = self.product_pool.lookup(term)
                if hit:
                    label, how, ids = hit
                    return Match(kind, term, label, how, len(ids), k), set(ids), []
            else:
                hit, sug = self.pools[k].lookup(term)
                if hit:
                    label, how = hit
                    ids = self.pools[k].ids_for(label)
                    return Match(kind, term, label, how, len(ids), k), set(ids), []
            if sug and not suggestions:
                suggestions = sug
        return None, set(), suggestions

    def resolve(self, f: Filters) -> Resolution:
        res = Resolution()
        for level, attr in (
            (_PRODUCT_LEVEL, "product_ids"),
            (_STORE_LEVEL, "store_ids"),
        ):
            constraint: Optional[set[int]] = None
            parts: list[str] = []
            for kind in level:
                terms = getattr(f, kind)
                if not terms:
                    continue
                union: set[int] = set()
                labels: list[str] = []
                for term in terms:
                    match, ids, sug = self._find(kind, term, level)
                    if match is None:
                        res.unresolved.append(Unresolved(kind, term, sug))
                        continue
                    res.matches.append(match)
                    union |= ids
                    labels.append(match.label)
                if labels:
                    parts.append(" or ".join(labels))
                    constraint = union if constraint is None else constraint & union
            if constraint is not None:
                setattr(res, attr, sorted(constraint))
                if not constraint and res.ok:
                    what = "products" if attr == "product_ids" else "stores"
                    res.empty_reason = f"No {what} match all of: {' AND '.join(parts)}."
        return res
