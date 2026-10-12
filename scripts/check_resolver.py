"""Live check of the entity resolver against your database.  Run:  python -m scripts.check_resolver"""
from app.config import load_settings
from app.db import connect
from app.llm.schema import Filters
from app.repository import load_catalog
from app.semantic.resolver import EntityResolver


CASES = [
    ("Delhi stores", Filters(cities=["Delhi"])),
    ("Lenovo laptops", Filters(brands=["Lenovo"], categories=["laptops"])),
    ("Samsung + LG TVs", Filters(brands=["Samsung", "LG"], categories=["TV"])),
    ("city typed as a store", Filters(stores=["Delhi"])),
    ("Bangalore (alias)", Filters(cities=["Bangalore"])),
    ("North region", Filters(regions=["north"])),
    ("store by name", Filters(stores=["Saket"])),
    ("typo: Samsng", Filters(brands=["Samsng"])),
    ("partial: Sams", Filters(brands=["Sams"])),
    ("unknown brand", Filters(brands=["Zebra"])),
    ("Dell tablets (none exist)", Filters(brands=["Dell"], categories=["tablets"])),
    ("model code", Filters(products=["LEN-LAP-1"])),
    ("iPhone 16 (spec example)", Filters(products=["iPhone 16"])),
    ("Lenovo model X (spec example)", Filters(products=["Lenovo Model X"])),
]


def main() -> None:
    conn = connect(load_settings())
    try:
        products, stores = load_catalog(conn)
    finally:
        conn.close()
    print(f"Catalog: {len(products)} products, {len(stores)} stores")
    print("Brands:", ", ".join(sorted(products.brand.unique())))
    print("Categories:", ", ".join(sorted(products.category.unique())))
    print("Cities:", ", ".join(sorted(stores.city.unique())), "\n")

    resolver = EntityResolver(products, stores)
    for title, f in CASES:
        r = resolver.resolve(f)
        n_p = "all" if r.product_ids is None else len(r.product_ids)
        n_s = "all" if r.store_ids is None else len(r.store_ids)
        print(f"{title}: ok={r.ok} products={n_p} stores={n_s}")
        for line in r.explain():
            print("   ", line)
        if not r.ok:
            print("    ask:", r.clarification_question())
        print()


if __name__ == "__main__":
    main()
