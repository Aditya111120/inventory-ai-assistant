# Business Rules V1

## Source and scope

At application startup, `app/repository.py` reads `rule_key` and `rule_value` from `dbo.BusinessRules` and creates `app.calc.metrics.Rules`. The defaults below are the fallback values declared in code and are documented as the current V1 defaults; deployed table values may differ and should be checked in the approved environment. This documentation does not validate live database values.

`Rules.resolve()` currently supports only global rules. Non-global override data is not active in V1.

## Rules

| Rule | V1 code default | Formula/use | Interpretation and limits |
|---|---:|---|---|
| `sales_window_days` | 30 days | Sales units are summed in the configured rolling window; average daily sales = units / window days. | Must be positive. Query windows use an exclusive start and inclusive end. Zero sales yields no DOI rather than an infinite value. |
| `excess_days_of_inventory` | 60 days | Legacy DOI excess flag: quantity must be positive and DOI must be greater than the threshold. | Strict `>` comparison. This compatibility flag is separate from inventory-vs-maximum-level classifications. |
| `slow_moving_units_window` | 3 units | Candidate when sales units in the configured demand window are less than this threshold. | Strict `<` comparison. Negative or missing demand is treated separately as a data-quality/unknown case in query enrichment. |
| `stockout_critical_days` | 7 days | Coverage band is Critical when DOI is less than the threshold. | Strict `<`; this is a coverage band, not the supplier lead-time risk classification. |
| `stockout_warning_days` | 14 days | Coverage band is Warning when DOI is at least critical threshold and less than warning threshold. | Strict `<` at warning boundary. At/above the boundary the coverage band is OK. |
| `safety_buffer_days` | 7 days | Medium stockout risk if DOI covers supplier lead time but is less than lead time plus safety buffer. | High if DOI < lead time; Medium if DOI < lead time + buffer; otherwise Low. Unknown DOI or invalid/missing lead time receives a non-numeric status rather than Low. |

## Related calculation semantics

- **Days of inventory (DOI):** `quantity_on_hand / average_daily_sales`. In the vectorized query flow, average daily sales is `units_sold_window / window_days`; DOI is null when average sales is zero.
- **Inventory value:** `quantity_on_hand * cost_price`.
- **Availability status:** zero quantity means `Out of Stock`; negative/non-finite quantity is invalid. Reorder and maximum thresholds are validated independently; missing or inconsistent thresholds produce explicit unknown/invalid labels.
- **Legacy stock status:** compares quantity with stored reorder and max levels (`Low`, `Excess`, otherwise `Healthy`). It is not the same as the newer availability status.
- **What-if:** output is marked `Simulated`; it changes scenario inputs in memory and does not update SQL Server records.
- **Actual vs simulated:** ordinary database-backed answers are marked Actual. Scenario results are explicitly Simulated.

## Aggregation cautions

- Calculate DOI from aggregated quantity and sales over the matching scope/window, not by averaging store-level DOI values.
- For product-store analysis, the grain is a product at one store. Company and region summaries must aggregate source quantities and sales before computing ratios.
- A product with no recent sales has undefined DOI; do not describe it as having zero days of stock.
- Supplier lead time is product-specific in the operational query and can be unavailable or conflicting when multiple primary supplier rows exist. Treat the query's selected lead time and data-quality notes as evidence, not as a verified contractual delivery promise.
- Snapshot-backed historical inventory is point-in-time quantity. It does not provide historical DOI, reorder status, or supplier risk unless separate source data supports those calculations.

The rules are intentionally not configurable by category, subcategory, product, or store in V1. Any scoped-rule design requires business approval and a separately reviewed migration/calculation change.

