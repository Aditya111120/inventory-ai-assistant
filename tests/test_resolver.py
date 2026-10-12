import pandas as pd
import pytest

from app.llm.schema import Filters
from app.semantic.resolver import EntityResolver

PAIRS = [
    ("Apple", "Smartphones"),
    ("Apple", "Laptops"),
    ("Apple", "Tablets"),
    ("Apple", "Audio"),
    ("Samsung", "Smartphones"),
    ("Samsung", "Televisions"),
    ("Samsung", "Tablets"),
    ("Samsung", "Audio"),
    ("LG", "Televisions"),
    ("LG", "Audio"),
    ("LG", "Appliances"),
    ("Sony", "Televisions"),
    ("Sony", "Audio"),
    ("Sony", "Cameras"),
    ("Dell", "Laptops"),
    ("HP", "Laptops"),
    ("Lenovo", "Laptops"),
    ("Lenovo", "Tablets"),
    ("Asus", "Laptops"),
    ("OnePlus", "Smartphones"),
    ("OnePlus", "Audio"),
    ("Xiaomi", "Smartphones"),
    ("Xiaomi", "Televisions"),
    ("boAt", "Audio"),
    ("Canon", "Cameras"),
]
SINGULAR = {
    "Smartphones": "Smartphone",
    "Laptops": "Laptop",
    "Televisions": "TV",
    "Tablets": "Tablet",
    "Cameras": "Camera",
    "Appliances": "Appliance",
    "Audio": "Audio",
}
STORES = [
    (1, "Connaught Place", "Delhi", "North"),
    (2, "Saket", "Delhi", "North"),
    (3, "Nehru Place", "Delhi", "North"),
    (4, "Andheri", "Mumbai", "West"),
    (5, "Bandra", "Mumbai", "West"),
    (6, "Lower Parel", "Mumbai", "West"),
    (7, "Indiranagar", "Bengaluru", "South"),
    (8, "Koramangala", "Bengaluru", "South"),
    (9, "Whitefield", "Bengaluru", "South"),
    (10, "T Nagar", "Chennai", "South"),
    (11, "Velachery", "Chennai", "South"),
    (12, "Salt Lake", "Kolkata", "East"),
    (13, "Park Street", "Kolkata", "East"),
    (14, "MI Road", "Jaipur", "North"),
]


@pytest.fixture(scope="module")
def r():
    rows, pid = [], 0
    for brand, cat in PAIRS:
        for n in range(1, 7):
            pid += 1
            code = f"{brand[:3].upper()}-{cat[:3].upper()}-{n}"
            rows.append(
                (
                    pid,
                    f"SKU-{pid:04d}",
                    f"{brand} {SINGULAR[cat]} {code}",
                    brand,
                    cat,
                    code,
                )
            )
    prods = pd.DataFrame(
        rows,
        columns=["product_id", "sku", "product_name", "brand", "category", "model"],
    )
    stores = pd.DataFrame(
        STORES, columns=["store_id", "store_name", "city", "region"]
    )
    res = EntityResolver(prods, stores)
    res.prods, res.stores = prods, stores
    return res


def test_no_filters_means_no_restriction(r):
    x = r.resolve(Filters())
    assert x.product_ids is None and x.store_ids is None and x.ok and not x.empty


def test_brand_case_insensitive_and_counts(r):
    x = r.resolve(Filters(brands=["lenovo"]))
    assert (
        len(x.product_ids) == 12
        and x.matches[0].label == "Lenovo"
        and x.matches[0].how == "exact"
    )
    assert r.resolve(Filters(brands=["BOAT"])).matches[0].label == "boAt"


def test_explicit_filter_mentions_are_catalog_backed_across_dimensions(r):
    mentions = r.explicit_filter_mentions(
        "What about Lenovo Laptop LEN-LAP-1 in Mumbai at Bandra?"
    )

    assert mentions.brands == ["Lenovo"]
    assert mentions.products == ["Lenovo Laptop LEN-LAP-1"]
    assert mentions.cities == ["Mumbai"]
    assert mentions.stores == ["Bandra"]


def test_explicit_category_alias_is_grounded_to_catalog_category(r):
    mentions = r.explicit_filter_mentions("What about laptops?")

    assert mentions.categories == ["Laptops"]


def test_brand_and_category_are_anded_within_field_ored(r):
    x = r.resolve(Filters(brands=["Samsung", "LG"], categories=["TV"]))
    got = r.prods[r.prods.product_id.isin(x.product_ids)]
    assert (
        set(got.brand) == {"Samsung", "LG"}
        and set(got.category) == {"Televisions"}
        and len(got) == 12
    )


def test_category_synonyms_and_plurals(r):
    for term, cat in [
        ("TV", "Televisions"),
        ("laptops", "Laptops"),
        ("laptop", "Laptops"),
        ("phones", "Smartphones"),
        ("headphones", "Audio"),
        ("Cameras", "Cameras"),
    ]:
        assert r.resolve(Filters(categories=[term])).matches[0].label == cat, term


def test_city_alias_and_store_fallback_to_city(r):
    x = r.resolve(Filters(cities=["Bangalore"]))
    assert x.store_ids == [7, 8, 9] and x.matches[0].how == "alias"
    y = r.resolve(Filters(stores=["Delhi"]))
    assert y.store_ids == [1, 2, 3] and y.matches[0].found_as == "cities"
    assert "given as store, found as city" in " ".join(y.explain())


def test_region_forms_and_partial_store_name(r):
    assert r.resolve(Filters(regions=["north india"])).store_ids == [1, 2, 3, 14]
    assert r.resolve(Filters(regions=["South"])).store_ids == [7, 8, 9, 10, 11]
    x = r.resolve(Filters(stores=["Connaught"]))
    assert x.store_ids == [1] and x.matches[0].how == "partial"


def test_store_and_city_levels_are_anded(r):
    assert r.resolve(Filters(cities=["Delhi"], regions=["West"])).store_ids == []


def test_fuzzy_auto_accept_noted(r):
    x = r.resolve(Filters(brands=["Samsng"]))
    assert x.ok and x.matches[0].label == "Samsung" and x.matches[0].how == "fuzzy"
    assert "[fuzzy match]" in x.explain()[0]


def test_geographic_near_match_is_not_auto_accepted(r):
    # A country/unknown place must not be silently converted into a store.
    x = r.resolve(Filters(stores=["India"]))
    assert not x.ok
    assert x.unresolved[0].kind == "stores"
    assert x.store_ids is None


def test_close_but_not_close_enough_asks_did_you_mean(r):
    x = r.resolve(Filters(brands=["Sams"]))
    assert not x.ok and "Samsung" in x.unresolved[0].suggestions
    assert "Did you mean Samsung" in x.clarification_question()


def test_unknown_term_is_unresolved_without_guessing(r):
    x = r.resolve(Filters(brands=["Zebra"]))
    assert (
        not x.ok
        and x.unresolved[0].suggestions == []
        and "Zebra" in x.clarification_question()
    )
    assert x.product_ids is None


def test_partial_resolution_keeps_what_worked(r):
    x = r.resolve(Filters(brands=["Dell"], cities=["Atlantis"]))
    assert not x.ok and len(x.product_ids) == 6 and x.store_ids is None


def test_empty_intersection_has_a_reason(r):
    x = r.resolve(Filters(brands=["Dell"], categories=["tablets"]))
    assert x.product_ids == [] and x.empty and "Dell AND Tablets" in x.empty_reason


def test_product_by_model_code_sku_and_name(r):
    p = r.prods.iloc[0]
    for term in (p.model, p.sku, p.product_name, p.model.lower()):
        x = r.resolve(Filters(products=[term]))
        assert x.product_ids == [int(p.product_id)], term


def test_product_words_match_several_and_nonsense_does_not(r):
    x = r.resolve(Filters(products=["Dell Laptop"]))
    assert len(x.product_ids) == 6 and x.matches[0].how == "partial"
    assert not r.resolve(Filters(products=["iPhone 16"])).ok


def test_brand_word_typed_as_a_product_still_finds_that_brands_products(r):
    x = r.resolve(Filters(products=["Lenovo"]))
    assert len(x.product_ids) == 12


def test_explain_lines_are_human_readable(r):
    lines = r.resolve(Filters(cities=["Delhi"], brands=["Lenovo"])).explain()
    assert any("Delhi" in line and "3 stores" in line for line in lines)
    assert any("Lenovo" in line and "12 products" in line for line in lines)


def test_subcategory_resolution_uses_assigned_values_only():
    products = pd.DataFrame([
        {"product_id": 1, "sku": "A", "product_name": "Soundbar A", "brand": "Sony", "category": "Audio", "model": "A", "subcategory": "Soundbar"},
        {"product_id": 2, "sku": "B", "product_name": "Audio B", "brand": "Sony", "category": "Audio", "model": "B", "subcategory": None},
        {"product_id": 3, "sku": "C", "product_name": "Speaker C", "brand": "LG", "category": "Audio", "model": "C", "subcategory": "Speaker"},
    ])
    stores = pd.DataFrame([{"store_id": 1, "store_name": "Central", "city": "Chennai", "region": "South"}])
    result = EntityResolver(products, stores).resolve(Filters(subcategories=["Soundbar"]))
    assert result.product_ids == [1]
    assert result.matches[0].found_as == "subcategories"


def test_category_word_soundbar_prefers_real_subcategory():
    products = pd.DataFrame([
        {"product_id": 1, "sku": "A", "product_name": "Soundbar A", "brand": "Sony", "category": "Audio", "model": "A", "subcategory": "Soundbar"},
        {"product_id": 2, "sku": "B", "product_name": "Audio B", "brand": "Sony", "category": "Audio", "model": "B", "subcategory": None},
    ])
    stores = pd.DataFrame([{"store_id": 1, "store_name": "Central", "city": "Chennai", "region": "South"}])
    result = EntityResolver(products, stores).resolve(Filters(categories=["soundbar"]))
    assert result.product_ids == [1]


def test_unclassified_subcategory_filter_returns_only_null_assignments():
    products = pd.DataFrame([
        {"product_id": 1, "sku": "A", "product_name": "Soundbar A", "brand": "Sony", "category": "Audio", "model": "A", "subcategory": "Soundbar"},
        {"product_id": 2, "sku": "B", "product_name": "Audio B", "brand": "Sony", "category": "Audio", "model": "B", "subcategory": None},
    ])
    stores = pd.DataFrame([{ "store_id": 1, "store_name": "Central", "city": "Chennai", "region": "South" }])
    result = EntityResolver(products, stores).resolve(Filters(subcategories=["without subcategory"]))
    assert result.product_ids == [2]


def test_catalog_brand_mentions_returns_only_exact_catalog_labels():
    products = pd.DataFrame([
        {"product_id": 1, "sku": "A", "product_name": "Phone A", "brand": "Apple", "category": "Smartphones", "model": "A", "subcategory": None},
        {"product_id": 2, "sku": "B", "product_name": "Phone B", "brand": "Samsung", "category": "Smartphones", "model": "B", "subcategory": None},
        {"product_id": 3, "sku": "C", "product_name": "Speaker C", "brand": "boAt", "category": "Audio", "model": "C", "subcategory": None},
    ])
    stores = pd.DataFrame([{"store_id": 1, "store_name": "Central", "city": "Chennai", "region": "South"}])
    resolver = EntityResolver(products, stores)

    assert resolver.catalog_brand_mentions("Compare Apple and Samsung sales") == ["Apple", "Samsung"]
    assert resolver.catalog_brand_mentions("Compare a pineapple brand and boats") == []
