"""Build the review-only 150-product subcategory manifest."""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAPPING = ROOT / "PRODUCT_MASTER_SUBCATEGORY_MAPPING.csv"
REVIEW = ROOT / "PRODUCT_SUBCATEGORY_APPROVAL_REVIEW.csv"
CATALOG = Path(r"C:\Users\sai.telaprolu\Downloads\inventory_product_catalog.csv")
OUTPUT = ROOT / "PRODUCT_SUBCATEGORY_COMPLETE_MANIFEST.csv"

VERIFIED = {
    10: "Speaker", 11: "Air conditioner", 36: "Refrigerator", 58: "Soundbar",
    60: "Soundbar", 61: "Washing machine", 83: "Speaker", 85: "Soundbar",
    86: "Dryer", 88: "Soundbar",
}


def read(path: Path) -> dict[int, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {int(row["product_id"]): row for row in csv.DictReader(fh)}


def main() -> None:
    mapping = read(MAPPING)
    catalog = read(CATALOG)
    review = read(REVIEW)
    if set(mapping) != set(catalog) or len(catalog) != 150:
        raise SystemExit("Catalog/mapping does not reconcile to exactly 150 product IDs")
    conflicts = []
    rows = []
    for product_id in sorted(catalog):
        source, proposed = catalog[product_id], mapping[product_id]
        if any(source[k] != proposed[k] for k in ("sku", "product_name", "brand", "category")):
            conflicts.append(product_id)
        candidate = review.get(product_id, {})
        if product_id in VERIFIED:
            status, approval, existing = "EXISTING_VERIFIED", "VERIFIED_EXISTING", VERIFIED[product_id]
        elif proposed.get("proposed_subcategory"):
            status, approval, existing = "PENDING_REVIEW", "PENDING", ""
        else:
            status, approval, existing = "UNRESOLVED", "NOT_APPLICABLE", ""
        rows.append({
            "product_id": str(product_id), "sku": source["sku"], "product_name": source["product_name"],
            "brand": source["brand"], "category": source["category"], "existing_subcategory": existing,
            "proposed_subcategory": proposed.get("proposed_subcategory", ""),
            "classification_status": status, "approval_status": approval,
            "evidence": candidate.get("evidence", proposed.get("classification_evidence", "")),
            "review_notes": candidate.get("review_notes", proposed.get("review_notes", "")),
        })
    if conflicts:
        raise SystemExit(f"Mapping/catalog conflicts for product IDs: {conflicts}")
    with OUTPUT.open("w", encoding="utf-8", newline="") as fh:
        fields = list(rows[0])
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUTPUT.name}")


if __name__ == "__main__":
    main()
