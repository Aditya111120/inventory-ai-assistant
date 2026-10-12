# Product Subcategory Approval Review

**Status:** Approval review only — no classification decisions or database writes made  
**Source:** `PRODUCT_MASTER_SUBCATEGORY_MAPPING.csv` reconciled to the supplied catalog

## Candidate totals

| Support classification | Count | Meaning |
|---|---:|---|
| Clearly supported | 13 | The literal product name supports the proposed subtype, subject to taxonomy-owner approval. |
| Ambiguous | 24 | The name suggests a subtype, but form factor, variant identity, or controlled vocabulary needs confirmation. |
| Unsupported | 3 | The supplied name does not defensibly establish the proposed subtype. |
| **Total candidates** | **40** | Every row remains pending approval. |

The remaining 110 of 150 products have no defensible additional subcategory
from the supplied name and remain unclassified (`NULL`).

## Decision policy

- `approval_decision` is `PENDING` for all 40 candidates.
- Confidence values are evidence indicators, not business approval.
- `approval_source` is intentionally blank until an owner records the approving
  source or decision reference.
- No row is executable by the existing loader until explicitly approved and
  copied into its controlled manifest.
- Variant 2 rows require confirmation that they are distinct sellable products
  and what dimension differentiates them.
- `AirPods Max`, Sony WH/WF families, XBOOM/ULT families, and foldable-device
  taxonomy require authoritative catalog or owner confirmation before approval.

## Deliverable

The row-level review is in
`PRODUCT_SUBCATEGORY_APPROVAL_REVIEW.csv`. It includes product identity,
category, proposed subtype, evidence, confidence, review notes,
`support_classification`, `approval_decision`, and `approval_source`.

No SQL Server connection, classification load, schema change, BusinessRules
change, or application change was performed.
