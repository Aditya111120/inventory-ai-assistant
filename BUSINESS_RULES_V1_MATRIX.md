# InventoryAI Business Rules V1 — Implemented Matrix

The V1 calculation path remains deterministic and uses the existing global
`BusinessRules` values. No rule values, database objects, product
classifications, or fact data were changed.

| Area | Implemented output | Boundary / quality behavior |
|---|---|---|
| Availability | `inventory_status` | Negative = Invalid Stock Quantity; zero = Out of Stock; positive quantity at/below reorder = At or Below Reorder Level; above maximum = Above Maximum Level; otherwise Within Stock Limits. |
| Threshold quality | `inventory_data_quality` | Missing/negative reorder or maximum, maximum below reorder. |
| Demand | `demand_status` | 0 = No Recorded Sales; 1–2 = Slow-moving Candidate; 3+ = Recorded Sales; negative/missing = Demand Unknown. |
| Demand quality | `demand_data_quality` | Negative or missing recorded sales quantity is flagged. |
| DOI | `days_of_inventory` | Quantity divided by recorded units over the selected observation window; undefined when demand is not positive. |
| Risk | `stockout_risk_v1` | High/Medium/Low by DOI, lead time, and safety buffer; no demand, invalid inputs, and out-of-stock remain not assessable/independent statuses. |
| Supplier | `supplier_rule`, `supplier_data_quality` | Single valid primary; duplicate same lead; maximum lead for differing primaries; no valid/invalid primary is flagged. |
| Excess | `excess_by_max_level`, `excess_coverage_candidate`, `no_recorded_sales_review` | Maximum-level excess and DOI > configured excess threshold are separate; zero-sales stock is not silently confirmed excess by the V1 flags. |
| Historical | Snapshot/date notes | Sales use parameterized periods; historical inventory uses weekly snapshots and reports the actual snapshot date. |

The legacy `stock_status`, `stockout_risk`, `is_excess`, and `is_slow_moving`
columns remain for compatibility. New V1 columns provide the approved labels
without breaking existing capabilities.

## Acceptance questions

1. “Show inventory availability for all stores.” Expect independent inventory status and threshold-quality fields.
2. “Which products are out of stock?” Expect zero quantity regardless of threshold quality.
3. “Which positions are at or below reorder?” Expect equality included.
4. “Show products with invalid inventory thresholds.” Expect machine-readable quality flags.
5. “Which products had no recorded sales in the last 30 days?” Expect `No Recorded Sales`.
6. “Which products are slow-moving?” Expect only 1–2 recorded units under the approved window.
7. “Which products are at high stockout risk?” Expect DOI below valid supplier lead time.
8. “Show stockout risk where supplier lead time is missing.” Expect `Unknown`/quality flag, not Low.
9. “Show excess coverage candidates.” Expect DOI above the configured threshold only when DOI exists.
10. “Show August 2026 inventory.” Expect weekly snapshot quantity with the actual snapshot date; no current-inventory substitution.
