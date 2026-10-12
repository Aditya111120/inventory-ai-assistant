# InventoryAI Test Migration — Read-only Preflight Report

**Date:** 2026-10-09  
**Status:** Blocked before migration; no SQL migration was executed

## Runner correction

The runner previously hardcoded `Encrypt=yes`, which did not match the verified
local connection setting. It now accepts `DB_ENCRYPT` with values `yes`, `no`,
or `optional`, retaining `yes` as the secure default. The local preflight used:

```text
DB_ENCRYPT=optional
```

The generated connection string was verified as:

```text
Encrypt=optional;TrustServerCertificate=yes
```

## Verified/operator-supplied facts

| Item | Value | Status |
|---|---|---|
| SQL Server instance | `SWGL056\\SQLEXPRESS01` | Supplied; connection could not be established from this environment |
| Approved test database | `InventoryAI_Test` | Supplied; database identity/status not independently verified |
| Protected database | `InventoryAI` | Not connected to or modified |
| Backup | `C:\\Program Files\\Microsoft SQL Server\\MSSQL17.SQLEXPRESS01\\MSSQL\\Backup\\InventoryAI.bak` | Path and verification supplied by operator |
| Backup verification | `RESTORE VERIFYONLY` passed | Operator-provided; not rerun |
| Backup checksum | Not enabled | Operator-provided |
| Test files | `InventoryAI_Test.mdf`, `InventoryAI_Test_log.ldf` | Operator-provided; absolute paths not verified |

## Attempted read-only preflight

The runner-compatible Windows Authentication connection was attempted with:

```text
DB_SERVER=SWGL056\SQLEXPRESS01
DB_NAME=InventoryAI_Test
DB_AUTH=windows
DB_DRIVER=ODBC Driver 18 for SQL Server
DB_TRUST_SERVER_CERT=yes
```

The corrected connection still failed before any query executed:

```text
ODBC 08001: Encryption not supported on the client / SSL Provider: No credentials are available in the security package; server not found or not accessible.
```

No protected database connection was attempted. No restore, migration, classification load, rollback, or write operation occurred.

## Checks not completed

Because the connection failed, the following remain unverified:

- Actual `@@SERVERNAME`, `DB_NAME()`, and database online status.
- Required table existence and table counts.
- SQL Server Agent jobs referencing `InventoryAI_Test`.
- Database-level/object triggers and external data sources.
- Linked-server or other observable external integrations.
- Application configuration isolation.
- Product/fact counts, fingerprints, catalog fingerprint, BusinessRules baseline,
  FK integrity, schema metadata, and aggregate values.
- Migration execution, post-migration validation, and idempotency.

## Readiness decision

**Not ready for migration execution from this environment.** The blocker is
connectivity/Windows-authentication access to `SWGL056\\SQLEXPRESS01`, not a
reported migration-script failure. Do not switch to `InventoryAI` or another
database to work around the failure.

## Required next action

An operator with access to the approved SQL Server host should verify the ODBC
Driver 18 installation, Windows security context, SQL Server protocol/instance
availability, and encryption policy. Then rerun the existing read-only
preflight and validation commands against exactly `InventoryAI_Test`. If the
environment requires SQL authentication, provide credentials only through the
process environment; never place them in this report or `.env`.

The schema migration must remain pending until identity, isolation, baseline,
and recovery prerequisites all pass. Unit tests for the correction pass (`13
passed`), but no live SQL Server preflight result was obtained.
