from __future__ import annotations

import re
from collections import defaultdict

from app.config import load_settings
from app.db import connect


PRODUCT_TEMPLATES = {
    "Apple": {
        "Smartphones": [
            "iPhone 16",
            "iPhone 16 Plus",
            "iPhone 16 Pro",
            "iPhone 16 Pro Max",
        ],
        "Laptops": [
            "MacBook Air M3",
            "MacBook Pro 14 M3",
            "MacBook Pro 16 M3",
            "MacBook Air 15 M3",
        ],
        "Tablets": [
            "iPad Air",
            "iPad Pro 11",
            "iPad Pro 13",
            "iPad 10th Gen",
        ],
        "Audio": [
            "AirPods Pro 2",
            "AirPods 4",
            "AirPods Max",
            "Beats Studio Buds+",
        ],
    },
    "Samsung": {
        "Smartphones": [
            "Galaxy S25",
            "Galaxy S25 Ultra",
            "Galaxy A56",
            "Galaxy Z Flip6",
        ],
        "Laptops": [
            "Galaxy Book5 Pro",
            "Galaxy Book5",
            "Galaxy Book4 Ultra",
            "Galaxy Book4 Pro",
        ],
        "Tablets": [
            "Galaxy Tab S10",
            "Galaxy Tab S10 Ultra",
            "Galaxy Tab S9 FE",
            "Galaxy Tab A9+",
        ],
        "Televisions": [
            "Neo QLED 4K QN90D",
            "OLED S90D 4K TV",
            "Crystal UHD 4K DU8000",
            "Neo QLED 8K QN900D",
        ],
        "Audio": [
            "Galaxy Buds3 Pro",
            "Galaxy Buds3",
            "HW-Q990D Soundbar",
            "Music Frame Speaker",
        ],
    },
    "LG": {
        "Televisions": [
            "OLED evo C4 4K TV",
            "OLED B4 4K TV",
            "QNED 4K TV",
            "NanoCell 4K TV",
        ],
        "Laptops": [
            "LG gram 16",
            "LG gram 14",
            "LG Ultra PC 16",
            "LG gram Pro 17",
        ],
        "Audio": [
            "XBOOM Go Speaker",
            "XBOOM XL7",
            "Soundbar S90TR",
            "Soundbar S80QY",
        ],
        "Appliances": [
            "DualCool Inverter AC",
            "Smart Refrigerator",
            "Front Load Washer",
            "HeatPump Dryer",
        ],
    },
    "Lenovo": {
        "Laptops": [
            "ThinkPad E14 Gen 6",
            "ThinkPad E16 Gen 2",
            "ThinkPad T14 Gen 5",
            "ThinkPad T16 Gen 3",
            "IdeaPad Slim 5",
            "IdeaPad Pro 5",
            "Yoga Slim 7",
            "Yoga 7i",
        ],
        "Tablets": [
            "Tab P12",
            "Tab M11",
            "Tab Plus",
            "Legion Tab",
        ],
        "Smartphones": [
            "Moto Edge 50 Pro",
            "Moto Edge 50 Fusion",
            "Moto G85",
            "Moto G55",
        ],
    },
    "Dell": {
        "Laptops": [
            "XPS 13",
            "XPS 14",
            "Inspiron 14",
            "Inspiron 16",
            "Latitude 5450",
            "Latitude 7450",
            "Vostro 3520",
            "G15 Gaming Laptop",
        ],
        "Monitors": [
            "UltraSharp U2724D",
            "UltraSharp U3224KB",
        ],
        "Tablets": [
            "Dell Tablet 11",
            "Dell Tablet Pro",
        ],
    },
    "HP": {
        "Laptops": [
            "Spectre x360 14",
            "Envy x360 14",
            "Pavilion Plus 14",
            "EliteBook 840 G11",
            "ProBook 440 G11",
            "Victus 16",
            "OMEN 16",
            "15s Laptop",
        ],
        "Monitors": [
            "E27 G5",
            "E24 G5",
        ],
    },
    "Sony": {
        "Televisions": [
            "BRAVIA 8 OLED 4K",
            "BRAVIA 7 Mini LED 4K",
            "BRAVIA 3 4K",
            "BRAVIA XR A95L",
        ],
        "Audio": [
            "WH-1000XM5",
            "WF-1000XM5",
            "ULT Field 7",
            "HT-A7000 Soundbar",
        ],
        "Cameras": [
            "Alpha 7 IV",
            "Alpha 6700",
            "ZV-E10 II",
            "Cyber-shot RX100 VII",
        ],
    },
    "Canon": {
        "Cameras": [
            "EOS R6 Mark II",
            "EOS R8",
            "EOS R50",
            "PowerShot G7 X Mark III",
        ],
    },
    "Xiaomi": {
        "Smartphones": [
            "Xiaomi 14",
            "Xiaomi 14 Ultra",
            "Redmi Note 14 Pro",
            "Redmi Note 14",
        ],
        "Tablets": [
            "Xiaomi Pad 7",
            "Redmi Pad Pro",
            "Xiaomi Pad 6S Pro",
        ],
    },
    "OnePlus": {
        "Smartphones": [
            "OnePlus 13",
            "OnePlus 13R",
            "OnePlus Nord 4",
            "OnePlus Nord CE4",
        ],
        "Audio": [
            "OnePlus Buds Pro 3",
            "OnePlus Buds 3",
            "OnePlus Nord Buds 3 Pro",
        ],
        "Tablets": [
            "OnePlus Pad 2",
            "OnePlus Pad Go",
        ],
    },
    "Asus": {
        "Laptops": [
            "Zenbook 14 OLED",
            "Vivobook S 14",
            "ROG Zephyrus G14",
            "ROG Strix G16",
            "TUF Gaming A15",
        ],
        "Monitors": [
            "ProArt PA279CRV",
            "ROG Swift OLED PG27AQDM",
        ],
    },
    "boAt": {
        "Audio": [
            "Airdopes 311 Pro",
            "Airdopes 141",
            "Rockerz 450",
            "Stone 1508",
            "Nirvana Ion",
        ],
    },
}


def normalize_category(category: str) -> str:
    value = str(category).strip().casefold()
    aliases = {
        "tv": "Televisions",
        "television": "Televisions",
        "tvs": "Televisions",
        "phone": "Smartphones",
        "mobile": "Smartphones",
        "smartphone": "Smartphones",
    }
    if value in aliases:
        return aliases[value]
    return str(category).strip().title()


def clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def main() -> None:
    settings = load_settings()
    conn = connect(settings)

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT product_id, brand, category, model, sku
            FROM dbo.Products
            ORDER BY product_id
            """
        )
        rows = cursor.fetchall()
        used_names: set[str] = set()
        counters = defaultdict(int)
        updates = []

        for row in rows:
            product_id = int(row.product_id)
            brand = str(row.brand)
            category = normalize_category(row.category)
            options = PRODUCT_TEMPLATES.get(brand, {}).get(category, [])
            index = counters[(brand, category)]

            if options:
                base = options[index % len(options)]
                cycle = index // len(options)
                candidate = (
                    f"{base} - Variant {cycle + 1}"
                    if cycle
                    else base
                )
            else:
                model = clean_name(str(row.model or ""))
                candidate = (
                    f"{brand} {model}"
                    if model
                    else f"{brand} {category} {product_id}"
                )

            candidate = clean_name(candidate)
            if candidate.casefold() in used_names:
                candidate = f"{candidate} ({str(row.sku).strip()})"

            used_names.add(candidate.casefold())
            counters[(brand, category)] += 1
            updates.append((candidate, product_id))

        cursor.executemany(
            """
            UPDATE dbo.Products
            SET product_name = ?
            WHERE product_id = ?
            """,
            updates,
        )
        conn.commit()
        print(f"Updated {len(updates)} product names.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
