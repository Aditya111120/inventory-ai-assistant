# Product Subcategory Reconciliation Report

**Status:** Review and controlled-loading preparation; no database writes performed  
**Source:** 150-row catalog, mapping, approval-review file, and the ten SSMS-verified assignments supplied by the project owner

## Reconciled totals

| Classification status | Count |
|---|---:|
| `EXISTING_VERIFIED` | 10 |
| `PENDING_REVIEW` | 30 |
| `UNRESOLVED` | 110 |
| **Total products** | **150** |

Approval status totals are `VERIFIED_EXISTING` = 10, `PENDING` = 30, and
`NOT_APPLICABLE` = 110. No new assignment is approved by this artifact.

## Existing assignments preserved

The ten supplied assignments are represented in
`PRODUCT_SUBCATEGORY_COMPLETE_MANIFEST.csv` as `EXISTING_VERIFIED` and are
not proposed for reload or correction:

`10 Speaker`, `11 Air conditioner`, `36 Refrigerator`, `58 Soundbar`,
`60 Soundbar`, `61 Washing machine`, `83 Speaker`, `85 Soundbar`,
`86 Dryer`, and `88 Soundbar`.

## Remaining candidate evidence

Of the 30 non-existing candidates:

- 3 are clearly supported by literal names, but still require explicit business approval.
- 24 are ambiguous and require taxonomy, form-factor, or Variant 2 confirmation.
- 3 are unsupported from the supplied evidence and should remain unresolved.

The 110 unresolved products have no defensible additional subtype from the
available names and remain without a proposed assignment.

## Integrity checks

- Catalog and mapping reconcile to exactly 150 product IDs.
- Product IDs, SKUs, product names, brands, and categories match the source
  catalog for every manifest row.
- No mapping conflicts were found.
- No technical specifications, lifecycle facts, product families, or variant
  dimensions were invented.
- Existing assignments are recorded only; they were not overwritten or reloaded.

## Loading boundary

The existing transactional/idempotent loader remains the controlled workflow.
It must receive only a separately approved manifest subset. The 30 pending rows
must not be loaded, and the 110 unresolved products must remain `NULL`.
Production loading requires separate authorization from test loading.

No SQL Server connection, schema change, classification load, BusinessRules
change, inventory/sales update, supplier update, purchase-order update, or
rollback was performed.

## Final enrichment analysis — 2026-10-09

The complete worksheet was reconciled against the 150-row catalog using
`product_id`, SKU, product name, brand, and category. The ten SSMS-verified
assignments remain unchanged and are marked `EXISTING_VERIFIED` with
`VERIFIED_EXISTING` approval status.

| Population | Count | Decision boundary |
|---|---:|---|
| Already classified and verified | 10 | Preserve exactly; no reload |
| Additional proposals with literal/name evidence | 30 | `PENDING_REVIEW`; business approval required |
| Unresolved products | 110 | Keep `subcategory_id` NULL |
| **Total** | **150** | No new assignment is approved by this artifact |

The 30 pending proposals include names that suggest a product form (for
example earbuds, headphones, speaker, soundbar, air conditioner, refrigerator,
or foldable smartphone), but they are not automatically approved. Rows with
`Variant 2` remain pending because the source does not establish the variant
dimension or prove that the row is a distinct sellable item.

### Consistent proposed taxonomy

The worksheet uses one conservative vocabulary below the existing broad
categories: `Earbuds`, `Headphones`, `Speaker`, `Soundbar`, `Air conditioner`,
`Refrigerator`, `Washing machine`, `Dryer`, and `Foldable smartphone`.
Smartphones, laptops, tablets, televisions, and cameras without explicit
subtype evidence remain unclassified. No RAM, storage, display, lens, bundle,
generation, lifecycle, family, or technical specification is inferred.

### Information missing for unresolved products

- Smartphones, laptops, tablets, and televisions with only a model name need an
  approved subtype taxonomy and authoritative catalog classification; the name
  alone does not establish a narrower class.
- Audio products such as `WH-*`, `WF-*`, `ULT`, `XBOOM`, `Rockerz`, `Stone`,
  or `Nirvana` need authoritative form-factor confirmation where the name does
  not explicitly identify the subtype.
- Cameras need an authoritative product-type classification (body, lens, kit,
  or other approved class); the name does not establish that distinction.
- Every `Variant 2` row needs a source-defined variant key or other approved
  differentiator before it can be treated as a distinct classified product.
- All unresolved lifecycle, family, and specification attributes remain
  unsupported by the supplied catalog and must not be guessed.

The updated 150-row worksheet is
`PRODUCT_SUBCATEGORY_COMPLETE_MANIFEST.csv`. It contains the explicit
`confidence` and `approval_status` fields required for review. No database or
application changes were performed.
