# Inventory Metrics and Business Rules

The application continues to load thresholds from `dbo.BusinessRules`; no
values were changed. Calculations use product-store inventory positions as the
operational grain.

## Inventory state

- `Low`: quantity on hand `< reorder_level`.
- `Healthy`: quantity on hand `>= reorder_level` and `<= max_level`.
- `Excess`: quantity on hand `> max_level`.
- Inventory value: `quantity_on_hand * cost_price`.

Boundary equality is intentional: quantities exactly at reorder or maximum
levels are Healthy.

## Coverage and risk

- Average daily sales: `units_sold_window / sales_window_days`.
- Days of inventory: `quantity_on_hand / average_daily_sales` when demand is
  positive; otherwise `NULL`.
- Zero stock with positive demand has zero coverage and is High risk when lead
  time is known.
- Zero stock with zero demand is `No demand`, not High risk.
- Positive stock with zero demand has `No demand` coverage; excess analysis may
  separately flag it under the existing excess-days rule.
- Known lead time: DOI `< lead_time` = High; DOI `< lead_time + safety_buffer`
  = Medium; otherwise Low.
- Missing/invalid supplier lead time = `Unknown lead time`, never Low or High.
- Missing supplier rows no longer remove inventory positions from results.
- Primary supplier lead time is reduced deterministically to the minimum when
  duplicate primary rows exist; no new threshold is introduced.

Inventory trend queries use the latest available snapshot on or before each
requested date. If no snapshot exists, the result is empty with an explicit
data-availability note; no stale-date threshold is invented.

## Scope and explanation

Queries retain product, store, city, and region columns. Subcategory joins are
left joins, so NULL/unclassified products remain available. Trust Panel
calculations use the same deterministic values shown in the result. Simulated
what-if outputs are labeled separately from Actual data.
