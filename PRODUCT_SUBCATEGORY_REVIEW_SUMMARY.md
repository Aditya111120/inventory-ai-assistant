# Product Subcategory Review Summary

**Prepared:** 2026-10-08  
**Status:** Proposal for business review only — no database changes made

## Source and validation

Source: `C:\Users\sai.telaprolu\Downloads\inventory_product_catalog.csv`.

- 150 source rows and 150 mapping rows.
- 150 unique product IDs and 150 unique SKUs.
- No missing source products, extra mappings, duplicate IDs, or duplicate SKUs.
- Product ID/SKU pairs match exactly between source and mapping.
- Existing `product_name`, `brand`, and `category` values were not changed.
- No technical specifications, lifecycle facts, family relationships, or business approvals were inferred.

## What changed from the previous mapping

The previous mapping used labels such as `Laptop - named model`, `Smartphone - named model`, and `Television - named model` for nearly every row. Those labels added little analytical value, so the revised mapping leaves unsupported subcategories empty.

The revised mapping keeps only conservative, name-supported proposals:

| Proposed subcategory | Count | Confidence profile | Analytical value |
|---|---:|---|---|
| Earbuds | 21 | Medium | Useful for audio assortment and future policy grouping; confirm form factor and variants. |
| Speaker | 6 | High/Low | Useful where the name says Speaker; XBOOM/ULT family rows remain low confidence. |
| Soundbar | 4 | High | Useful for audio inventory and coverage grouping; confirm controlled vocabulary. |
| Air conditioner | 2 | High | Meaningfully separates appliance replenishment behavior. |
| Refrigerator | 2 | High | Meaningfully separates appliance replenishment behavior. |
| Washing machine | 1 | High | Controlled singular vocabulary; parent category remains Appliances. |
| Dryer | 1 | High | Meaningfully separates appliance replenishment behavior. |
| Headphones | 2 | Medium | Sony WH family suggests the subtype; authoritative confirmation required. |
| Foldable smartphone | 1 | Medium | `Z Flip` supports a form-factor proposal; confirm taxonomy. |
| Unclassified / empty | 110 | Low | No defensible additional subtype from the supplied name alone. |
| **Total** | **150** |  |  |

The mapping file contains the required review fields: product ID, SKU, product name, brand, category, proposed subcategory, classification evidence, confidence, review status, and review notes.

## Ambiguous products requiring review

- All `Variant 2` rows: the suffix does not establish RAM, storage, color, generation, bundle, or any other variant dimension.
- Xiaomi `XIA-TEL-*` rows: generated-looking labels; retain the source name unchanged and leave subcategory unclassified.
- Sony `WH-*` and `WF-*`: provisional headphone/earbud classifications require an authoritative catalog check.
- boAt `Airdopes`, `Rockerz`, `Stone`, and `Nirvana`: family names suggest audio forms but do not consistently prove the controlled subtype.
- LG `XBOOM` and Sony `ULT`: speaker proposals are low confidence unless the authoritative catalog confirms the form factor.
- Camera models: no body/lens/bundle distinction is proposed.
- Laptop models: no business/gaming/ultrabook classification is proposed.

## Schema and migration position

For the first migration, propose only:

1. `ProductSubcategories` lookup table.
2. Nullable `subcategory_id` on `dbo.Products`.
3. Category-constrained foreign key and an index on nullable `subcategory_id`.

Do not add `ProductFamilies`, lifecycle fields, detailed specifications, or scoped business-rule thresholds in this migration. Preserve all product IDs, SKUs, names, inventory, sales, supplier links, purchase orders, and historical snapshots.

## Business-review readiness

The mapping is ready for business review, not production migration. Before implementation, approve the controlled vocabulary, resolve the ambiguous rows, document classification owners, and confirm that unclassified values remain `NULL` where evidence is insufficient.

## Migration blockers

- Business approval of the taxonomy is still required.
- Authoritative confirmation is required for medium/low-confidence audio and foldable classifications.
- Variant identity and lifecycle semantics are unresolved.
- No migration should proceed until the mapping is reconciled against the target database and pre/post preservation checks are approved.

No database connection, DDL, data update, or business-rule change was performed.
