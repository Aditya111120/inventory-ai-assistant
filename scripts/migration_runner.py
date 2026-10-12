"""Controlled Product Master migration runner.

Dry-run is the default and never opens a database connection.  Apply mode
requires --apply and a non-empty recovery-plan file.  Credentials are read
only from process environment variables; this module deliberately does not
load dotenv files or import app.config (which may load .env).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyodbc


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "PRODUCT_MASTER_MIGRATION_VALIDATION.sql"
SCHEMA = ROOT / "001_product_subcategories_schema.sql"
LOADER = ROOT / "002_product_subcategories_load_approved.sql"


class RunnerError(RuntimeError):
    """A safe, operator-actionable runner failure."""


def env_settings() -> dict[str, Any]:
    auth = os.getenv("DB_AUTH", "sql").strip().lower()
    server = os.getenv("DB_SERVER", "").split("#", 1)[0].strip()
    database = os.getenv("DB_NAME", "InventoryAI").split("#", 1)[0].strip() or "InventoryAI"
    driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server").split("#", 1)[0].strip()
    if not server:
        raise RunnerError("DB_SERVER is not set; provide it through the process environment.")
    if auth not in {"sql", "windows"}:
        raise RunnerError("DB_AUTH must be 'sql' or 'windows'.")
    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASSWORD", "")
    if auth == "sql" and not (user and password):
        raise RunnerError("DB_USER and DB_PASSWORD are required for DB_AUTH=sql.")
    encrypt = os.getenv("DB_ENCRYPT", "yes").split("#", 1)[0].strip().lower()
    if encrypt not in {"yes", "no", "optional"}:
        raise RunnerError("DB_ENCRYPT must be 'yes', 'no', or 'optional'.")
    return {"server": server, "database": database, "driver": driver, "auth": auth,
            "user": user, "password": password,
            "trust": os.getenv("DB_TRUST_SERVER_CERT", "yes").strip().lower() in {"1", "yes", "true"},
            "encrypt": encrypt}


def connection_string(cfg: dict[str, Any]) -> str:
    def quote(value: str) -> str:
        return "{" + value.replace("}", "}}") + "}"
    parts = [f"DRIVER={{{cfg['driver']}}}", f"SERVER={cfg['server']}", f"DATABASE={cfg['database']}"]
    if cfg["auth"] == "windows":
        parts.append("Trusted_Connection=yes")
    else:
        parts += [f"UID={quote(cfg['user'])}", f"PWD={quote(cfg['password'])}"]
    parts += [f"Encrypt={cfg['encrypt']}", f"TrustServerCertificate={'yes' if cfg['trust'] else 'no'}"]
    return ";".join(parts)


def safe_target(cfg: dict[str, Any]) -> str:
    return f"{cfg['server']}/{cfg['database']} ({cfg['auth']} authentication)"


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def execute_query(conn: pyodbc.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        result_sets: list[dict[str, Any]] = []
        while True:
            if cur.description:
                names = [item[0] for item in cur.description]
                rows = [{name: json_value(value) for name, value in zip(names, row)} for row in cur.fetchall()]
                result_sets.append({"columns": names, "rows": rows})
            if not cur.nextset():
                break
        return result_sets
    finally:
        cur.close()


def run_sql(conn: pyodbc.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8-sig")
    if re.search(r"^\s*GO\s*$", sql, re.IGNORECASE | re.MULTILINE):
        raise RunnerError(f"{path.name} contains GO batches; pyodbc execution was refused.")
    cur = conn.cursor()
    try:
        cur.execute(sql)
    finally:
        cur.close()


def active_approved_assignments(path: Path = LOADER) -> bool:
    text = path.read_text(encoding="utf-8-sig")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"--.*", "", text)
    return bool(re.search(r"\bINSERT\s+@Approved\s*\(", text, re.IGNORECASE))


def read_only_sql_is_safe(sql: str) -> bool:
    """Reject executable DML/DDL while allowing words in comments."""
    without_comments = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    without_comments = re.sub(r"--.*", "", without_comments)
    return not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|ALTER|DROP|CREATE|TRUNCATE|RESTORE|BACKUP)\b", without_comments, re.IGNORECASE)


def preflight_failures(result_sets: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for result in result_sets:
        columns = set(result.get("columns", []))
        rows = result.get("rows", [])
        if "orphan_rows" in columns:
            for row in rows:
                if int(row.get("orphan_rows") or 0) > 0:
                    failures.append("Foreign-key orphan rows detected")
        for key in ("duplicate_product_ids", "duplicate_skus"):
            if key in columns and rows and int(rows[0].get(key) or 0) > 0:
                failures.append(f"{key} is non-zero")
        if {"product_id", "sku", "category", "subcategory_id"} <= columns and rows:
            failures.append("Subcategory constraint violations detected")
    return failures


def compare_validation(pre: dict[str, Any], post: dict[str, Any]) -> list[str]:
    """Compare invariant result sets; migration metadata result sets are excluded."""
    failures: list[str] = []
    invariant_columns = {
        "product_id_sku_sha256", "product_catalog_sha256", "revenue_sum",
        "orphan_rows", "duplicate_product_ids", "duplicate_skus",
    }
    for result in pre.get("result_sets", []):
        columns = set(result.get("columns", []))
        if not columns.intersection(invariant_columns) and not ({"table_name", "row_count"} <= columns):
            continue
        matches = [x for x in post.get("result_sets", []) if x.get("columns") == result.get("columns")]
        if not matches or matches[0].get("rows") != result.get("rows"):
            failures.append("Invariant validation result changed: " + ",".join(result.get("columns", [])))
    return failures


def verify_database_identity(result_sets: list[dict[str, Any]], cfg: dict[str, Any]) -> None:
    identity = next((item for item in result_sets if item.get("columns", [])[:2] == ["database_name", "server_name"]), None)
    if not identity or not identity.get("rows"):
        raise RunnerError("Validation did not return database/server identity; refusing to continue.")
    row = identity["rows"][0]
    if str(row.get("database_name", "")).lower() != cfg["database"].lower():
        raise RunnerError("Connected database identity does not match DB_NAME; refusing to continue.")
    if not row.get("server_name"):
        raise RunnerError("Connected server identity is empty; refusing to continue.")


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled InventoryAI SQL Server migration runner")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="authorize schema changes")
    modes.add_argument("--preflight", action="store_true", help="run read-only identity and validation checks")
    modes.add_argument("--production-preflight", action="store_true",
                       help="run read-only checks against the protected production database")
    parser.add_argument("--authorize-classifications", action="store_true",
                        help="separately authorize the approved classification loader")
    parser.add_argument("--production-authorize", action="store_true",
                        help="explicitly authorize apply mode for InventoryAI only")
    parser.add_argument("--recovery-plan", type=Path,
                        help="non-empty backup/recovery plan file required with --apply")
    parser.add_argument("--expected-server", help="fail unless DB_SERVER exactly matches this value")
    parser.add_argument("--expected-database", help="fail unless DB_NAME exactly matches this value")
    parser.add_argument("--report", type=Path, default=Path("migration-report.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = env_settings()
        if args.expected_server and cfg["server"].lower() != args.expected_server.lower():
            raise RunnerError("Configured DB_SERVER does not match --expected-server.")
        if args.expected_database and cfg["database"].lower() != args.expected_database.lower():
            raise RunnerError("Configured DB_NAME does not match --expected-database.")
        if args.production_authorize and not args.apply:
            raise RunnerError("--production-authorize is valid only with --apply.")
        if args.production_preflight:
            if args.authorize_classifications or args.production_authorize:
                raise RunnerError("Production preflight cannot load classifications or authorize apply.")
            if not args.expected_server or not args.expected_database:
                raise RunnerError("--production-preflight requires --expected-server and --expected-database.")
            if args.expected_database.lower() != "inventoryai" or cfg["database"].lower() != "inventoryai":
                raise RunnerError("Production preflight is restricted to the exact InventoryAI database.")
            if not VALIDATION.is_file() or not read_only_sql_is_safe(VALIDATION.read_text(encoding="utf-8-sig")):
                raise RunnerError("Validation SQL failed the read-only safety check.")
            report: dict[str, Any] = {"mode": "production-preflight", "target": safe_target(cfg), "database_changes": False}
            try:
                with pyodbc.connect(connection_string(cfg), timeout=10) as conn:
                    conn.autocommit = False
                    result_sets = execute_query(conn, VALIDATION.read_text(encoding="utf-8-sig"))
                    verify_database_identity(result_sets, cfg)
                    failures = preflight_failures(result_sets)
                    report["validation"] = {"result_sets": result_sets}
                    report["failures"] = failures
                    write_report(args.report, report)
                    if failures:
                        print(f"PRODUCTION PREFLIGHT FAILED; report: {args.report}", file=sys.stderr)
                        return 2
            except (RunnerError, pyodbc.Error, OSError) as exc:
                report["error"] = str(exc)
                write_report(args.report, report)
                raise
            print(f"Production preflight passed; report: {args.report}")
            return 0
        if args.preflight:
            if args.authorize_classifications:
                raise RunnerError("--preflight cannot be combined with --authorize-classifications.")
            if not args.expected_server or not args.expected_database:
                raise RunnerError("--preflight requires --expected-server and --expected-database.")
            if cfg["database"].lower() == "inventoryai" or args.expected_database.lower() != "inventoryai_test":
                raise RunnerError("--preflight is restricted to the approved InventoryAI_Test database.")
            if not VALIDATION.is_file() or not read_only_sql_is_safe(VALIDATION.read_text(encoding="utf-8-sig")):
                raise RunnerError("Validation SQL failed the read-only safety check.")
            report: dict[str, Any] = {"mode": "preflight", "target": safe_target(cfg), "database_changes": False}
            try:
                with pyodbc.connect(connection_string(cfg), timeout=10) as conn:
                    conn.autocommit = False
                    result_sets = execute_query(conn, VALIDATION.read_text(encoding="utf-8-sig"))
                    verify_database_identity(result_sets, cfg)
                    failures = preflight_failures(result_sets)
                    report["validation"] = {"result_sets": result_sets}
                    report["failures"] = failures
                    write_report(args.report, report)
                    if failures:
                        print(f"PREFLIGHT FAILED; report: {args.report}", file=sys.stderr)
                        return 2
            except (RunnerError, pyodbc.Error, OSError) as exc:
                report["error"] = str(exc)
                write_report(args.report, report)
                raise
            print(f"Preflight passed; report: {args.report}")
            return 0
        if not args.apply:
            print(f"DRY RUN: target {safe_target(cfg)}")
            print("No database connection opened and no SQL executed. Use --apply only after review.")
            print(f"Schema file: {SCHEMA.name}; validation file: {VALIDATION.name}")
            if args.authorize_classifications:
                raise RunnerError("Classification authorization is meaningful only with --apply.")
            return 0
        if not args.expected_server or not args.expected_database:
            raise RunnerError("--apply requires both --expected-server and --expected-database to prevent target drift.")
        if cfg["database"].lower() == "inventoryai" and not args.production_authorize:
            raise RunnerError("Production apply requires separate --production-authorize authorization.")
        if cfg["database"].lower() != "inventoryai" and args.production_authorize:
            raise RunnerError("--production-authorize is valid only when DB_NAME is exactly InventoryAI.")
        if not args.recovery_plan or not args.recovery_plan.is_file() or not args.recovery_plan.read_text(encoding="utf-8").strip():
            raise RunnerError("--apply requires an existing, non-empty --recovery-plan file.")
        if not VALIDATION.is_file() or not SCHEMA.is_file() or not LOADER.is_file():
            raise RunnerError("Migration package is incomplete.")
        report: dict[str, Any] = {"target": safe_target(cfg), "classification_loaded": False}
        with pyodbc.connect(connection_string(cfg), timeout=10) as conn:
            # Keep driver behavior explicit; each migration SQL script owns its
            # transaction and includes TRY/CATCH rollback handling.
            conn.autocommit = False
            report["pre_validation"] = {"result_sets": execute_query(conn, VALIDATION.read_text(encoding="utf-8"))}
            verify_database_identity(report["pre_validation"]["result_sets"], cfg)
            run_sql(conn, SCHEMA)
            if args.authorize_classifications and active_approved_assignments():
                run_sql(conn, LOADER)
                report["classification_loaded"] = True
            elif args.authorize_classifications:
                print("Classification authorization supplied, but loader has no active approved assignments; skipped.")
            report["post_validation"] = {"result_sets": execute_query(conn, VALIDATION.read_text(encoding="utf-8"))}
            failures = compare_validation(report["pre_validation"], report["post_validation"])
            report["validation_failures"] = failures
            write_report(args.report, report)
            if failures:
                print(f"POST-MIGRATION VALIDATION FAILED; investigate, do not auto-rollback. Report: {args.report}", file=sys.stderr)
                return 2
        print(f"Migration completed and validated. Report: {args.report}")
        return 0
    except (RunnerError, pyodbc.Error, OSError) as exc:
        print(f"Migration stopped safely: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
