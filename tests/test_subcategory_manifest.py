import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def rows():
    with (ROOT / "PRODUCT_SUBCATEGORY_COMPLETE_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_complete_manifest_covers_all_products_and_preserves_verified_assignments():
    data = rows()
    assert len(data) == 150
    assert len({row["product_id"] for row in data}) == 150
    expected = {"10": "Speaker", "11": "Air conditioner", "36": "Refrigerator", "58": "Soundbar",
                "60": "Soundbar", "61": "Washing machine", "83": "Speaker", "85": "Soundbar",
                "86": "Dryer", "88": "Soundbar"}
    actual = {row["product_id"]: row["existing_subcategory"] for row in data if row["classification_status"] == "EXISTING_VERIFIED"}
    assert actual == expected


def test_manifest_does_not_promote_pending_or_unresolved_rows():
    data = rows()
    assert all(row["approval_status"] == "PENDING" for row in data if row["classification_status"] == "PENDING_REVIEW")
    assert all(row["approval_status"] == "NOT_APPLICABLE" for row in data if row["classification_status"] == "UNRESOLVED")
