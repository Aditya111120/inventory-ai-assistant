# Conversational Clarification Audit

**Scope:** Read-only review of the current intent, entity-resolution, orchestration,
API, and session-state implementation. No application, SQL Server, BusinessRules,
frontend, or `.env` changes were made.

## Current flow

```text
POST /chat
  -> app.api.create_app().chat()
       persist user transcript
       -> Orchestrator.handle()
            SessionMemory.previous_context()
            -> parse_intent(question, llm, context)
            -> time-window validation
            -> explicit capability clarification / out-of-scope gate
            -> EntityResolver.resolve(Filters)
                 unresolved entity? -> clarification response (no dispatch)
                 empty intersection? -> no_results response
            -> authorized_store_ids intersection
            -> dispatch capability/query
            -> narrative + trust/display/followups
            -> SessionMemory.update() after a successful result
       persist assistant narrative/error transcript
  -> ChatResponse JSON
```

The response router (`app/response_router.py:15`, `plan_response`) only selects
presentation modes. It does not decide whether clarification is required.

## Findings by requested area

### 1. Where clarification is decided

There are four current decision points in `Orchestrator.handle`
(`app/orchestrator.py:642`):

1. Intent parsing failure (`IntentError`) returns `status="clarification"`
   (`~668`), while LLM availability has its own error status (`~674`).
2. `_time_window_issue` (`app/orchestrator.py:191`) returns clarification for
   incomplete/invalid month, exact-date, date-range, or unsupported historical
   metric requests (`~714`).
3. An LLM-emitted `intent.capability == "clarification"` returns its free-text
   `clarification_question` (`~752`).
4. `EntityResolver.resolve()` returning `ok == False` returns its unresolved
   entity question (`~821`).

There is no post-resolution policy that asks a question when a valid city maps
to multiple stores.

### 2. Entity resolution

- `app/repository.py:21`, `load_catalog`, loads active products with a
  `LEFT JOIN` to `ProductSubcategories`, and loads `store_id`, `store_name`,
  `city`, and `region` from `dbo.Stores`.
- `app/semantic/resolver.py:251`, `EntityResolver.__init__`, builds pools for
  brands, categories, assigned subcategories, store names, cities, and regions.
  Store names support partial matching; cities support aliases.
- `app/semantic/resolver.py:277`, `_find`, first prefers an assigned
  subcategory for subtype terms, then falls back across the relevant product or
  store level. The special “unclassified” terms select products whose joined
  subcategory is NULL.
- `app/semantic/resolver.py:309`, `resolve`, ORs terms within a field and ANDs
  product-level fields with store-level fields. A city resolves to the complete
  set of matching store IDs; it is not treated as an ambiguity.

### 3. Follow-up context preservation

`app/session.py:14` defines `SessionState`. After a successful, non-empty
dispatch, `app/session.py:48`, `SessionMemory.update`, stores the last product
and store IDs plus capability, kind, metric, grouping, sorting, subject,
comparison mode, and scope. `app/session.py:96`, `previous_context`, exposes a
semantic summary to the LLM but deliberately does not expose database IDs.

`Orchestrator.handle` uses `uses_previous_result` to apply the stored IDs in
execution state (see the memory block after dispatch setup, around
`app/orchestrator.py:1025`). Numeric product IDs emitted by the LLM are removed
before resolution. A new turn that does not explicitly refer to the prior
result is forced to `uses_previous_result=False` (`~693`).

Clarification turns do not call `SessionMemory.update`; therefore there is no
pending clarification state to merge into the next turn.

### 4. Why “Show laptop inventory in Chennai” executes immediately

The prompt (`app/llm/prompts.py:10`) treats “laptop” as a category and
“Chennai” as a city. The parser therefore produces a normal inventory intent
with category/city filters, not `capability="clarification"`. The resolver
recognizes both terms: the category maps to product IDs and Chennai maps to all
Chennai store IDs. Since there is no unresolved entity and no empty
intersection, `Orchestrator.handle` proceeds through authorization and dispatch.

This is correct for an explicit city-wide request under the current semantics,
but it does not satisfy a product requirement that a city with multiple stores
must offer a store choice. That requirement is currently unimplemented.

### 5. Can store names be retrieved before the main query?

Yes. The resolver already holds the catalog DataFrame and store pool loaded by
`load_catalog` (`app/repository.py:21`). A Chennai option list can be generated
from `resolver.stores`/the city pool and filtered through
`user_context.authorized_store_ids` before dispatch. There is currently no
public resolver method that returns those concrete store names as clarification
options, and `Resolution` only carries IDs/match summaries.

### 6. Persistence of options and selected values

`OrchestratorResponse` (`app/orchestrator.py:37`) has only free-text `error` for
clarification; it has no structured option/token/pending-intent fields.
`ChatResponse` (`app/api.py:38`) likewise has no clarification-options field.
`app/api.py:193`, `chat`, stores only the assistant text and `{"status": ...}`
metadata in `ConversationStore`; `app/history.py` has no structured pending
clarification storage. Consequently, selected values are not persisted as a
first-class choice. A follow-up such as “Central” is parsed as a new question,
with only ordinary LLM/session context available.

### 7. Global and comparison bypass behavior

`app/llm/intent.py:67`, `parse_intent`, normalizes explicit global phrases
(`overall`, `across all stores`, `whole/entire catalog`, etc.) to disable
previous-result restriction. The prompt also defines broad population wording
as `uses_previous_result=false` (`app/llm/prompts.py:10`).

Comparison intents are represented by `question_kind="comparison"`,
`comparison_mode`, and `comparison_subject`; `plan_response` recognizes the
comparison kind. They are not subject to a store-choice gate today. Explicit
global/company-wide wording should continue to bypass any future location
clarification policy.

### 8. Partial missing information

If one supplied entity is unresolved, `Resolution` retains successful matches
but `ok` is false, and the orchestrator asks a clarification for the unresolved
term without dispatching (`tests/test_resolver.py:test_partial_resolution_keeps_what_worked`).
If an entity resolves but maps to several stores, it is considered complete and
executes. If the LLM omits a required dimension, there is no deterministic
missing-dimension check; the request executes at the available scope unless a
time rule or parser clarification intervenes.

## Time-aware clarification audit

### 9. Date, month, year, and range interpretation

`app/llm/schema.py:64`, `TimeWindow`, represents `none`, relative windows
(`last_n_days`, `last_n_months`, `this_month`, `last_month`), `latest`,
`exact_date`, `month`, and `date_range`. The LLM prompt
(`app/llm/prompts.py:10`, `build_messages`) instructs the model to put time in
this field and to use `compare_to_previous` for period comparisons. It does not
define a timezone or an explicit “today” normalization contract.

`app/semantic/queries.py:102`, `window_for`, deterministically converts a
validated intent into inclusive/exclusive SQL boundaries. Exact dates use one
day; month windows use the calendar month; ranges use the supplied endpoints;
relative windows use the `as_of` date. Sales `as_of` is obtained from
`Queries.as_of` (`app/semantic/queries.py:167`) as `MAX(Sales.sale_date)`.

`app/orchestrator.py:191`, `_time_window_issue`, validates date shape and range
ordering before dispatch. A month with no year produces “Which year should I
use for that month?” (`~195`); missing/invalid exact dates and incomplete or
reversed ranges also produce deterministic clarification. The LLM supplies the
structured values, but this validation and decision is application code.

There is no explicit year-only `TimeWindow` kind. A month-only request is
handled only if the parser emits `kind="month"` with a missing year. If the
model instead emits `none` or another valid kind, the deterministic validator
does not recover the ambiguity.

### 10. Follow-up time and scope context

`app/orchestrator.py:70`, `_intent_summary`, stores only the time kind (and
relative `n`), not the exact date, year, month, start/end dates, comparison
period, or resolved product/location labels. `SessionMemory.previous_context`
(`app/session.py:96`) therefore cannot reliably preserve a prior year/month or
date range for a follow-up. Product/store IDs are retained internally after a
successful result, but the structured time window is not. The LLM may infer
some context from the short semantic summary, but this is not deterministic.

### 11. Current inventory versus historical snapshots

`app/dispatch.py:125`, `_inventory_dispatch`, routes `exact_date`, `month`, and
`date_range` to `Queries.inventory_snapshot`; all other inventory windows use
the current inventory-position path. `app/semantic/queries.py:233`,
`inventory_snapshot`, queries `InventorySnapshotWeekly` only and never
substitutes the current inventory table.

For a requested target, `inventory_snapshot` selects
`MAX(snapshot_date) <= target` and notes both the selected snapshot and the
requested-through date (`app/semantic/queries.py:254-269`). Thus an exact-date
request can silently use an earlier weekly snapshot; the user receives a note,
but there is no confirmation question such as “use the 2026-09-28 snapshot?”
and no tolerance rule for how far earlier that snapshot may be.

If no snapshot exists on or before the target, the query returns an empty result
with a limitation note. Future dates are not rejected up front; they simply
select the latest snapshot at or before that future target. Sales dates are
parameterized correctly, but availability is not prevalidated against MIN/MAX
sales dates, and there is no separate snapshot-availability check before the
main query.

### 12. Historical stockout-risk handling

`_time_window_issue` deliberately rejects exact-date, month, and range requests
for `inventory_health`, `stockout_risk`, `excess_slow_moving`, and `what_if`
(`app/orchestrator.py:219-225`) with a deterministic limitation: historical
inventory metrics require an approved weekly snapshot and current inventory is
not substituted. This prevents fabricated historical DOI/risk, but it is a
capability limitation rather than a choice between available historical data
and a confirmation path.

### 13. Determinism assessment

The following decisions are deterministic after parsing: date formatting and
range ordering, rejection of historical risk metrics, sales-window boundaries,
and selection of the latest weekly snapshot at or before a target. The LLM
currently decides the initial interpretation of natural-language dates,
whether a month has a year, and whether a follow-up refers to prior time
context. It also phrases capability clarification. Therefore the current
system is not a fully deterministic “LLM only phrases” clarification design.

## Unified clarification-engine recommendation

Add one deterministic clarification stage between intent parsing and dispatch,
with four ordered checks:

```text
Parsed intent
  -> entity resolution (product/category/subcategory/location)
  -> time normalization + availability probe (date/month/range/snapshot)
  -> capability/metric support check (including historical-risk policy)
  -> authorization + ambiguity policy
  -> structured clarification OR dispatch
```

Recommended contract:

- Normalize parsed dates into a canonical interval with timezone, explicit
  source (`exact`, `month`, `range`, `relative`, `latest`), and requested
  comparison period.
- Preserve the complete canonical intent (including year/month/range,
  products, subcategories, city/store, and metric) in pending session state.
- Probe available sales and weekly-snapshot bounds before dispatch. If an exact
  date maps to an earlier snapshot, return a structured confirmation option
  with requested date, selected snapshot date, and allowed action; do not
  silently proceed.
- Return deterministic reason codes and safe options for missing year, missing
  location/store, ambiguous entities, unsupported historical risk, and no data.
  The LLM may turn that result into friendly wording but may not choose the
  options or alter the canonical values.
- On a partial follow-up, merge only the supplied missing fields into the
  pending intent; ask again only for unresolved fields. Explicit global and
  comparison requests remain bypasses where their scope is unambiguous.

This engine should be covered by tests for month-without-year, exact-date
snapshot confirmation, unavailable/future dates, range ordering, historical
stockout-risk rejection, year/month/product/location follow-up preservation,
and deterministic option selection. It can be implemented without changing
SQL schemas, BusinessRules, metric formulas, or snapshot semantics.

## Gap analysis

| Gap | Current behavior | Consequence |
|---|---|---|
| Ambiguous city-to-store scope | City resolves to every matching store | “Chennai” cannot prompt for Central/Egmore/etc. |
| Structured clarification | Free-text error only | UI cannot render safe option buttons or selected tokens. |
| Pending clarification state | Not stored in `SessionState` or transcript metadata | A partial answer cannot deterministically complete the original request. |
| Authorization-aware options | Authorization is applied after resolution | Future options must filter before display to avoid exposing stores. |
| Required-scope policy | No capability/location matrix | Inventory questions with omitted or broad locations execute by default. |
| Time context persistence | Only time kind/relative count is retained | Year, month, exact date, and date-range follow-ups can lose their period. |
| Snapshot confirmation | Earlier `MAX(snapshot_date) <= target` is selected with a note | Exact-date inventory can answer from an earlier week without user confirmation. |
| Data availability validation | Empty/no-snapshot handling occurs inside the query | No preflight distinction between unsupported, unavailable, and future requests. |
| LLM boundary | LLM chooses initial temporal structure | Clarification is not yet limited to deterministic decisions plus wording. |
| Restart durability | `SessionMemory` is process-local | Pending choices and prior IDs disappear on process restart. |
| Clarification tests | Cover parser/unresolved entities, not city ambiguity or option selection | The principal requested behavior lacks regression coverage. |

## Smallest implementation recommendation (not implemented)

1. Add a deterministic policy function invoked after intent parsing and entity
   resolution but before dispatch. For location-sensitive capabilities, if a
   city resolves to more than one authorized store and the user did not say
   “all/across every store/company-wide/by city,” return clarification with the
   concrete authorized store options. Preserve explicit comparisons and global
   requests as bypasses.
2. Extend the response contract with a structured clarification object:
   `kind`, `prompt`, `options[{label, store_id}]`, and a short-lived pending
   intent/scope token. Keep free-text `error` for compatibility.
3. Add `pending_clarification` to `SessionState`, and persist the same metadata
   with the conversation record if choices must survive process restarts. Never
   expose internal IDs to the LLM; resolve an option token on the server.
4. On the next turn, first match a selected option against pending authorized
   options, merge it into the pending intent, and dispatch only after all
   required dimensions are satisfied. If the reply is partial or ambiguous,
   ask only for the remaining dimension.
5. Add regression tests for city-with-multiple-stores, explicit all-store and
   comparison bypasses, authorized option filtering, exact option selection,
   partial answers, session isolation, and restart behavior.

These changes are intentionally limited to clarification policy and state; they
do not alter SQL, metrics, BusinessRules, persona calculations, or existing
entity-matching semantics.

## Verification

Focused tests covering the inspected paths were run without database access:

```text
67 passed, 2 warnings
```

Warnings are environment/dependency warnings (Starlette/httpx deprecation and
pytest cache-path creation); no test failure was observed. No migration,
classification load, rollback, or SQL Server operation was executed.

**Conclusion:** Entity matching and ordinary unresolved-entity clarification are
working. Deterministic multi-store clarification, structured options, and
pending-choice persistence remain the implementation blockers for the requested
conversational behavior.
