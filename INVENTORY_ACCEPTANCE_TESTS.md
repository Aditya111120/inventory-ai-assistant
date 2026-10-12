# Inventory Acceptance Questions

These checks are intended for a database-backed test run. Results must use
actual data and the current `BusinessRules` values.

1. **Show inventory visibility for Chennai stores.**  Expect product-store
   rows with store and city, quantity, reorder/max levels, and concise totals.
2. **Which products are below reorder level in Chennai?**  Expect only rows
   where quantity is strictly below reorder level.
3. **Show soundbar inventory in Chennai.**  Expect only products assigned to
   the Soundbar subcategory and Chennai locations; unclassified Audio products
   must not appear as soundbars.
4. **Which speakers are below reorder level?**  Expect actual Speaker
   assignments, Low status, and affected store/city context.
5. **Show stockout risk for soundbars by store.**  Expect product-store DOI,
   lead time, risk, and the configured sales window.
6. **What is the stockout risk for a product with zero stock and positive sales?**
   Expect zero DOI and High risk when lead time is known.
7. **What is the stockout risk for a product with zero stock and zero sales?**
   Expect No demand, not High risk.
8. **What is the risk when supplier lead time is missing?**  Expect Unknown
   lead time and an explicit limitation, never an inferred Low/High result.
9. **Show refrigerators by store and city.**  Expect assigned Refrigerator
   products with product-store granularity and no DOI averaging across stores.
10. **Show products without subcategory assignments.**  Expect only products
    whose actual left-joined subcategory is NULL.
