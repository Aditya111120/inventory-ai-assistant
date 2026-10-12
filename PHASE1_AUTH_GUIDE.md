# Phase 1 authentication and role access

## Apply schema and demo principals

Review `SQL Files/Phase1_Auth_Migration.sql`, then execute it in the approved
`InventoryAI` database. It creates `AppRoles`, `AppUsers`,
`UserRegionAccess`, `UserBrandAccess`, and the server-side `AppSessions` table.
It is transactional and idempotent, refuses to overwrite password hashes, and
does not modify inventory, sales, products, stores, snapshots, or BusinessRules.

## Initialize passwords

From the backend project directory, with the normal process environment already
configured:

```powershell
python scripts/init_password.py SM-01
```

Repeat once for each approved account. Passwords are entered through a hidden
prompt, hashed with Argon2id-compatible Argon2, and never written to SQL or
logs. There is no shared default password.

## Session behavior

`POST /auth/login` sets an HttpOnly opaque cookie. `GET /auth/me` returns safe
identity information. `POST /auth/logout` revokes the server-side session.
Sessions are stored as SHA-256 token hashes with server-side expiry. Set
`AUTH_COOKIE_SECURE=false` only for explicitly configured localhost HTTP
development; keep it true for HTTPS deployments.

## Role scope

Store managers are restricted by assigned region. Brand managers are restricted
by assigned brands. Planner and Leadership are unrestricted. Scope is derived
from the authenticated session, not from React fields, LLM output, or prior
conversation context. Product restrictions are applied before dispatch and
aggregation; store restrictions continue through the existing SQL parameter
path.

The migration and password initialization were not executed by this change.
