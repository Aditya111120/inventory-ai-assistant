# Architecture

## Request path

1. **React client** (`frontend/src/App.jsx`) sends chat requests to FastAPI. The client also renders tables, clarification choices, conversation history, and the Trust Panel.
2. **FastAPI API** (`app/api.py`) validates request/response shapes, resolves the authenticated user context, calls the orchestrator, serializes supported database values, and stores conversation messages.
3. **Authentication and scope** (`app/auth.py`, `app/context.py`) validate server-side session tokens, resolve role-specific store/product access, and provide the scope used by the request. Session tokens are generated randomly; only token hashes are stored in SQL Server. Logout revokes the matching server-side session.
4. **Orchestrator** (`app/orchestrator.py`) obtains an intent, resolves entities, applies the server-owned authorization scope, requests clarification when required, invokes query/dispatch functions, and assembles the answer and evidence.
5. **LLM integration** (`app/llm/`) parses natural-language intent and may support optional narration. Business metrics, winners, and SQL parameters are determined by application code, not by the LLM.
6. **Semantic resolver and queries** (`app/semantic/resolver.py`, `app/semantic/queries.py`) resolve names from loaded catalogs and build parameterized SQL Server queries. Query dimensions and operators come from allowlists.
7. **Dispatch and deterministic calculations** (`app/dispatch.py`, `app/calc/metrics.py`) execute capability-specific query flows and compute metrics from returned rows and Business Rules V1.
8. **Conversation history** (`app/history.py`) stores users/conversations/messages in a local SQLite file. It is separate from SQL Server business data and can contain response evidence; local history files must not be published.

## Data flow and responsibility

```text
React (Vite)
    -> FastAPI /chat
        -> AuthService -> user and authorized scope (SQL Server)
        -> Orchestrator
            -> Intent parser (LLM integration)
            -> EntityResolver (in-memory catalog)
            -> Dispatch -> Queries (parameterized SQL Server SELECTs)
            -> deterministic calculation and answer/evidence assembly
        -> ConversationStore (local SQLite)
    <- JSON response
```

FastAPI startup constructs the production orchestrator, opens the configured SQL Server connection, loads Business Rules V1 and product/store catalogs, and creates the query/resolver components. SQL migrations are not applied during startup.

## Main modules

| Module | Responsibility |
|---|---|
| `app/api.py` | `/health`, `/chat`, `/auth/*`, and conversation endpoints; request/response serialization. |
| `app/orchestrator.py` | Intent-to-result flow, clarifications, scope, answer composition, and trust evidence. |
| `app/semantic/resolver.py` | Matching product, brand, category, store, city, and region labels from catalog frames. |
| `app/semantic/queries.py` | Parameterized query templates and supported reporting periods. |
| `app/dispatch.py` | Capability selection and query/calculation pipeline. |
| `app/calc/metrics.py` | Inventory classifications, DOI, risk, excess/slow movement, and scenario formulas. |
| `app/repository.py` | Reads rules and catalogs from SQL Server. |
| `app/auth.py` | SQL-backed login, sessions, role policy, and authorization scope. |
| `app/history.py` | User-scoped SQLite transcript persistence. |
| `frontend/src/App.jsx` | Chat UI, auth UX, clarification controls, history selection, and rendering. |

## Runtime boundaries

- SQL Server is the source of business records and active rule values.
- Local SQLite is only application conversation history; it is not an analytical database.
- The product catalog is loaded into memory for name resolution; requests still use SQL for operational facts.
- Current API CORS origins are local development origins. Production origins require an explicit deployment configuration/change.
- Database schema and account provisioning must be supplied by the database owner; repository migrations only cover named additive features and are not a complete base-schema installer.

