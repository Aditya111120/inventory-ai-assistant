"""Live Phase 1 acceptance runner.

The runner deliberately uses the real HTTP API and a read-only SQL connection.
It never creates a database, changes application data, or stores credentials.
Missing infrastructure or credentials produces ``BLOCKED`` rather than a
misleading partial pass.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

ACCOUNTS = ("SM-01", "SM-02", "SM-03", "SM-04", "BM-01", "BM-02", "PL-01", "LD-01")
PROTECTED_DATABASES = {"inventoryai"}


@dataclass
class CaseResult:
    account: str
    question: str
    expected: str
    actual: str = ""
    status: str = "BLOCKED"
    root_cause: str = ""


@dataclass
class RunReport:
    status: str = "BLOCKED"
    target: dict[str, str] = field(default_factory=dict)
    sql_identity: dict[str, Any] = field(default_factory=dict)
    account_scope: dict[str, Any] = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target,
            "sql_identity": self.sql_identity,
            "account_scope": self.account_scope,
            "cases": [c.__dict__ for c in self.cases],
            "blockers": self.blockers,
            "notes": self.notes,
        }


class ReadOnlySql:
    """Small SQL verifier. Every statement is a SELECT and autocommit is on."""

    def __init__(self, server: str, database: str, driver: str, encrypt: str, trust: str):
        self.server, self.database = server, database
        self.driver, self.encrypt, self.trust = driver, encrypt, trust
        self.conn = None

    def connect(self) -> None:
        if self.database.casefold() in PROTECTED_DATABASES and os.getenv("PHASE1_ALLOW_PRODUCTION") != "yes":
            raise RuntimeError("Refusing protected InventoryAI without PHASE1_ALLOW_PRODUCTION=yes")
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError("pyodbc is not installed") from exc
        # The runner is a separate process and must not read backend .env.
        # Match the documented .env.example default (SQL auth) unless the
        # operator explicitly supplies PHASE1_DB_AUTH/DB_AUTH.
        auth = os.getenv("PHASE1_DB_AUTH", os.getenv("DB_AUTH", "sql")).casefold()
        parts = [f"DRIVER={{{self.driver}}}", f"SERVER={self.server}", f"DATABASE={self.database}"]
        if auth == "sql":
            user = os.getenv("PHASE1_DB_USER") or os.getenv("DB_USER")
            password = os.getenv("PHASE1_DB_PASSWORD") or os.getenv("DB_PASSWORD")
            if not user or not password:
                raise RuntimeError("SQL authentication requires PHASE1_DB_USER and PHASE1_DB_PASSWORD")
            parts += [f"UID={user}", f"PWD={password}"]
        else:
            parts.append("Trusted_Connection=yes")
        parts += [f"Encrypt={self.encrypt}", f"TrustServerCertificate={self.trust}"]
        self.conn = pyodbc.connect(";".join(parts), autocommit=True, timeout=10)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.conn is None:
            raise RuntimeError("SQL connection is not open")
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            names = [d[0] for d in cur.description or ()]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    def identity(self) -> dict[str, Any]:
        rows = self.query("SELECT @@SERVERNAME AS server_name, DB_NAME() AS database_name, DATABASEPROPERTYEX(DB_NAME(),'Status') AS database_status")
        if not rows:
            raise RuntimeError("SQL identity query returned no row")
        return rows[0]

    def scopes(self) -> dict[str, dict[str, list[Any]]]:
        rows = self.query("""
            SELECT u.username, r.role_code, a.region, CAST(NULL AS nvarchar(100)) AS brand
            FROM dbo.AppUsers u JOIN dbo.AppRoles r ON r.role_id=u.role_id
            LEFT JOIN dbo.UserRegionAccess a ON a.user_id=u.user_id
            WHERE u.is_active=1
            UNION ALL
            SELECT u.username, r.role_code, CAST(NULL AS nvarchar(100)), a.brand
            FROM dbo.AppUsers u JOIN dbo.AppRoles r ON r.role_id=u.role_id
            LEFT JOIN dbo.UserBrandAccess a ON a.user_id=u.user_id
            WHERE u.is_active=1
        """)
        out = {a: {"role": "", "regions": [], "brands": []} for a in ACCOUNTS}
        for row in rows:
            name = str(row["username"])
            if name not in out:
                continue
            out[name]["role"] = str(row["role_code"])
            if row.get("region") is not None:
                out[name]["regions"].append(str(row["region"]))
            if row.get("brand") is not None:
                out[name]["brands"].append(str(row["brand"]))
        for item in out.values():
            item["regions"] = sorted(set(item["regions"]))
            item["brands"] = sorted(set(item["brands"]))
        return out

    def stores(self) -> list[dict[str, Any]]:
        return self.query("SELECT store_id, store_name, city, region FROM dbo.Stores")

    def products(self) -> list[dict[str, Any]]:
        return self.query("SELECT product_id, product_name, brand, category, sku FROM dbo.Products WHERE is_active=1")


class AcceptanceRunner:
    def __init__(self, api_url: str, expected_server: str, expected_database: str, sql: ReadOnlySql, credentials: dict[str, str]):
        self.api_url = api_url.rstrip("/")
        self.expected_server, self.expected_database = expected_server, expected_database
        self.sql, self.credentials = sql, credentials
        self.report = RunReport(target={"server": expected_server, "database": expected_database, "api_url": self.api_url})

    @staticmethod
    def credentials_from_env() -> dict[str, str]:
        result: dict[str, str] = {}
        raw = os.getenv("PHASE1_TEST_CREDENTIALS_JSON")
        if raw:
            try:
                data = json.loads(raw)
                result.update({str(k): str(v) for k, v in data.items()})
            except (TypeError, ValueError) as exc:
                raise RuntimeError("PHASE1_TEST_CREDENTIALS_JSON is not valid JSON") from exc
        for account in ACCOUNTS:
            value = os.getenv(f"PHASE1_PASSWORD_{account.replace('-', '_')}")
            if value:
                result[account] = value
        return result

    def _case(self, account: str, question: str, expected: str) -> CaseResult:
        return CaseResult(account=account, question=question, expected=expected)

    def _login(self, client: requests.Session, account: str) -> dict[str, Any]:
        password = self.credentials.get(account)
        if not password:
            raise RuntimeError(f"Missing credential for {account}")
        response = client.post(f"{self.api_url}/auth/login", json={"username": account, "password": password}, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"Login failed for {account} (HTTP {response.status_code})")
        return response.json()

    @staticmethod
    def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return payload.get("display_rows") or payload.get("table") or payload.get("response", {}).get("display_rows", [])

    def _assert_authorized(self, payload: dict[str, Any], account: str, scopes: dict[str, Any], stores: list[dict[str, Any]]) -> None:
        allowed_brands = {str(x).casefold() for x in scopes[account]["brands"]}
        allowed_regions = {str(x).casefold() for x in scopes[account]["regions"]}
        store_by_id = {str(s["store_id"]): s for s in stores}
        for row in self._rows(payload):
            brand = row.get("brand")
            if allowed_brands and brand is not None and str(brand).casefold() not in allowed_brands:
                raise AssertionError(f"unauthorized brand returned: {brand}")
            region = row.get("region")
            if allowed_regions and region is not None and str(region).casefold() not in allowed_regions:
                raise AssertionError(f"unauthorized region returned: {region}")
            sid = row.get("store_id")
            if sid is not None and str(sid) in store_by_id and allowed_regions:
                if str(store_by_id[str(sid)].get("region", "")).casefold() not in allowed_regions:
                    raise AssertionError(f"unauthorized store returned: {sid}")
        labels = [str(o.get("label", "")) for o in payload.get("clarification_options", [])]
        for label in labels:
            if allowed_brands and any(label.casefold() == str(b).casefold() for b in {str(p) for p in scopes[account]["brands"]}):
                continue
            for store in stores:
                if label.casefold() == str(store.get("store_name", "")).casefold():
                    if allowed_regions and str(store.get("region", "")).casefold() not in allowed_regions:
                        raise AssertionError(f"unauthorized store option returned: {label}")

    def _post(self, client: requests.Session, account: str, question: str, conversation_id: str) -> dict[str, Any]:
        response = client.post(f"{self.api_url}/chat", json={"question": question, "conversation_id": conversation_id, "session_id": conversation_id}, timeout=90)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} for {account}: request failed")
        return response.json()

    def run(self) -> RunReport:
        if self.expected_database.casefold() == "inventoryai" and os.getenv("PHASE1_ALLOW_PRODUCTION") != "yes":
            self.report.blockers.append("Protected InventoryAI requires PHASE1_ALLOW_PRODUCTION=yes; no production run is implicit")
            return self.report
        try:
            self.sql.connect()
            identity = self.sql.identity()
            self.report.sql_identity = identity
            if str(identity.get("server_name", "")).casefold() != self.expected_server.casefold() or str(identity.get("database_name", "")).casefold() != self.expected_database.casefold():
                raise RuntimeError("SQL identity does not match expected server/database")
            if str(identity.get("database_status", "")).casefold() != "online":
                raise RuntimeError("Target database is not online")
            scopes = self.sql.scopes()
            stores = self.sql.stores()
            products = self.sql.products()
            self.report.account_scope = scopes
            expected_roles = {
                **{a: "STORE_MANAGER" for a in ("SM-01", "SM-02", "SM-03", "SM-04")},
                **{a: "BRAND_MANAGER" for a in ("BM-01", "BM-02")},
                "PL-01": "PLANNER", "LD-01": "LEADERSHIP",
            }
            for account, role in expected_roles.items():
                if scopes.get(account, {}).get("role") != role:
                    raise RuntimeError(f"SQL role assignment mismatch for {account}")
        except Exception as exc:
            self.report.blockers.append(str(exc))
            return self.report
        missing = [a for a in ACCOUNTS if not self.credentials.get(a)]
        if missing:
            self.report.blockers.append("Missing credentials for: " + ", ".join(missing))
            return self.report
        try:
            health = requests.get(f"{self.api_url}/health", timeout=15)
            if health.status_code != 200:
                raise RuntimeError(f"API health returned HTTP {health.status_code}")
        except Exception as exc:
            self.report.blockers.append(f"API unavailable: {exc}")
            return self.report

        for account in ACCOUNTS:
            conversation = f"phase1-{account}-{uuid.uuid4().hex}"
            client = requests.Session()
            try:
                me = self._login(client, account)
                if me.get("username") != account:
                    raise AssertionError("authenticated identity mismatch")
                expected_scope = scopes[account]
                if me.get("role") != expected_scope["role"]:
                    raise AssertionError("/auth/login role does not match SQL assignment")
                if sorted(str(x) for x in me.get("regions", [])) != expected_scope["regions"]:
                    raise AssertionError("/auth/login regions do not match SQL assignment")
                if sorted(str(x) for x in me.get("brands", [])) != expected_scope["brands"]:
                    raise AssertionError("/auth/login brands do not match SQL assignment")
                # Broad authorized query: server authorization must happen before any clarification.
                question = "Show inventory across all stores"
                case = self._case(account, question, "Only authorized stores/products; no unauthorized clarification options")
                payload = self._post(client, account, question, conversation)
                if payload.get("status") in {"error", "out_of_scope"}:
                    raise AssertionError(f"authorized broad request denied: {payload.get('error') or payload.get('status')}")
                self._assert_authorized(payload, account, scopes, stores)
                case.actual = f"HTTP 200 status={payload.get('status')} rows={len(self._rows(payload))}"
                case.status = "PASS"
                self.report.cases.append(case)
                role = scopes[account]["role"]
                # Verify explicit unauthorized scope is denied before a broad
                # clarification can expose options. Full-access roles have no
                # unauthorized brand or region by design.
                if role == "STORE_MANAGER":
                    other = next((s for s in stores if str(s.get("region", "")).casefold() not in {r.casefold() for r in scopes[account]["regions"]}), None)
                    if other:
                        denied_q = f"Show inventory in {other.get('city')}"
                        denied = self._case(account, denied_q, "Denied before exposing unauthorized store choices")
                        denied_payload = self._post(client, account, denied_q, f"{conversation}-deny-region")
                        if denied_payload.get("status") not in {"error", "out_of_scope"} and not denied_payload.get("error"):
                            raise AssertionError("unauthorized region request was not denied")
                        denied.actual = f"status={denied_payload.get('status')}"
                        denied.status = "PASS"
                        self.report.cases.append(denied)
                elif role == "BRAND_MANAGER":
                    allowed = {b.casefold() for b in scopes[account]["brands"]}
                    other_brand = next((str(p.get("brand")) for p in products if p.get("brand") and str(p["brand"]).casefold() not in allowed), None)
                    if other_brand:
                        denied_q = f"Show {other_brand} inventory across all stores"
                        denied = self._case(account, denied_q, "Denied as unauthorized brand")
                        denied_payload = self._post(client, account, denied_q, f"{conversation}-deny-brand")
                        if denied_payload.get("status") not in {"error", "out_of_scope"} and not denied_payload.get("error"):
                            raise AssertionError("unauthorized brand request was not denied")
                        denied.actual = f"status={denied_payload.get('status')}"
                        denied.status = "PASS"
                        self.report.cases.append(denied)
                    for brand in scopes[account]["brands"]:
                        brand_q = f"Show {brand} inventory across all stores"
                        brand_case = self._case(account, brand_q, "Authorized brand returns only that brand")
                        brand_payload = self._post(client, account, brand_q, f"{conversation}-brand-{brand}")
                        self._assert_authorized(brand_payload, account, scopes, stores)
                        for row in self._rows(brand_payload):
                            if str(row.get("brand", "")).casefold() != brand.casefold():
                                raise AssertionError(f"brand filter leaked a non-{brand} row")
                        brand_case.actual = f"status={brand_payload.get('status')} rows={len(self._rows(brand_payload))}"
                        brand_case.status = "PASS"
                        self.report.cases.append(brand_case)
                followups = payload.get("followups", [])
                if followups:
                    follow_case = self._case(account, "click first Continue Exploring suggestion", "Suggestion executes and remains authorized")
                    follow_payload = self._post(client, account, str(followups[0]), conversation)
                    self._assert_authorized(follow_payload, account, scopes, stores)
                    follow_case.actual = f"status={follow_payload.get('status')}"
                    follow_case.status = "PASS"
                    self.report.cases.append(follow_case)
                # Explicit logout/revocation per isolated account.
                logout = client.post(f"{self.api_url}/auth/logout", timeout=30)
                if logout.status_code != 200:
                    raise AssertionError("logout did not return success")
                me_after = client.get(f"{self.api_url}/auth/me", timeout=30)
                if me_after.status_code != 401:
                    raise AssertionError("revoked session remained usable")
            except Exception as exc:
                failed = self._case(account, "account flow", "No authorization or session-isolation failure")
                failed.actual = "execution failed"
                failed.root_cause = str(exc)
                self.report.cases.append(failed)
            finally:
                client.close()

        # Account switching and cross-user conversation isolation.
        try:
            first_client, second_client = requests.Session(), requests.Session()
            first_conv = f"phase1-isolation-{uuid.uuid4().hex}"
            first_me = self._login(first_client, "SM-01")
            self._post(first_client, "SM-01", "Show inventory across all stores", first_conv)
            self._login(second_client, "SM-02")
            cross = second_client.get(f"{self.api_url}/conversations/{first_me.get('username')}/{first_conv}", timeout=30)
            isolation = self._case("SM-02", "read SM-01 conversation", "Cross-user conversation is denied")
            isolation.actual = f"HTTP {cross.status_code}"
            isolation.status = "PASS" if cross.status_code == 403 else "FAIL"
            if isolation.status == "FAIL":
                isolation.root_cause = "Conversation endpoint allowed cross-user access"
            self.report.cases.append(isolation)
            switched = self._case("SM-01 → BM-01", "logout then login as another account", "New account owns a fresh authenticated session")
            first_client.post(f"{self.api_url}/auth/logout", timeout=30)
            switched_login = self._login(first_client, "BM-01")
            switched_me = first_client.get(f"{self.api_url}/auth/me", timeout=30)
            if switched_login.get("username") != "BM-01" or switched_me.status_code != 200 or switched_me.json().get("username") != "BM-01":
                raise AssertionError("account switch retained the previous identity")
            switched.actual = "SM-01 revoked; /auth/me identifies BM-01"
            switched.status = "PASS"
            self.report.cases.append(switched)
        except Exception as exc:
            self.report.cases.append(CaseResult("SM-02", "read SM-01 conversation", "Cross-user conversation is denied", root_cause=str(exc)))
        finally:
            try: first_client.close(); second_client.close()
            except UnboundLocalError: pass

        # Targeted HP reproduction is deliberately data-driven: store must exist in SQL.
        hp = self._case("BM-02", "Show HP inventory in Chennai -> Anna Nagar -> Latest inventory", "All returned products are HP and store is authorized")
        try:
            anna = next((s for s in stores if str(s.get("store_name", "")).casefold() == "anna nagar" and str(s.get("city", "")).casefold() == "chennai"), None)
            if anna is None:
                raise RuntimeError("Anna Nagar/Chennai is not present in the read-only store catalog")
            client = requests.Session(); conversation = f"phase1-hp-{uuid.uuid4().hex}"
            self._login(client, "BM-02")
            first = self._post(client, "BM-02", "Show HP inventory in Chennai", conversation)
            options = first.get("clarification_options", [])
            option = next((o for o in options if str(o.get("label", "")).casefold() == "anna nagar"), None)
            if option is None:
                raise AssertionError("Anna Nagar was not offered as an authorized clarification option")
            second = self._post(client, "BM-02", str(option.get("token") or option.get("value") or option.get("label")), conversation)
            timeline = next((o for o in second.get("clarification_options", []) if "latest" in str(o.get("label", "")).casefold()), None)
            if timeline:
                final = self._post(client, "BM-02", str(timeline.get("token") or timeline.get("value") or timeline.get("label")), conversation)
            else:
                final = second
            self._assert_authorized(final, "BM-02", scopes, stores)
            for row in self._rows(final):
                if str(row.get("brand", "")).casefold() != "hp":
                    raise AssertionError("HP reproduction returned a non-HP product")
            hp.actual = f"status={final.get('status')} rows={len(self._rows(final))}"
            hp.status = "PASS"
        except Exception as exc:
            hp.actual = "execution failed"; hp.root_cause = str(exc)
        finally:
            try: client.close()
            except UnboundLocalError: pass
        self.report.cases.append(hp)
        self.report.status = "PASS" if self.report.cases and all(c.status == "PASS" for c in self.report.cases) else "FAIL"
        return self.report


def write_report(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
    md = path.with_suffix(".md")
    lines = [f"# Phase 1 Acceptance Report\n\nStatus: **{report.status}**", "", f"Target: `{report.target.get('server')}` / `{report.target.get('database')}`", "", "| Account | Question | Status | Actual | Root cause |", "|---|---|---|---|---|"]
    for c in report.cases:
        lines.append(f"| {c.account} | {c.question.replace('|','\\|')} | {c.status} | {c.actual.replace('|','\\|')} | {c.root_cause.replace('|','\\|')} |")
    if report.blockers:
        lines += ["", "## Blockers", ""] + [f"- {b}" for b in report.blockers]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live Phase 1 acceptance checks (read-only SQL, real HTTP API).")
    parser.add_argument("--api-url", default=os.getenv("PHASE1_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--expected-server", default=os.getenv("PHASE1_EXPECTED_SERVER"))
    parser.add_argument("--expected-database", default=os.getenv("PHASE1_EXPECTED_DATABASE"))
    parser.add_argument("--report", default="phase1_acceptance_report.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.expected_server or not args.expected_database:
        print("BLOCKED: --expected-server and --expected-database are required", file=sys.stderr)
        return 2
    credentials = AcceptanceRunner.credentials_from_env()
    sql = ReadOnlySql(args.expected_server, args.expected_database, os.getenv("PHASE1_DB_DRIVER", "ODBC Driver 18 for SQL Server"), os.getenv("PHASE1_DB_ENCRYPT", "optional"), os.getenv("PHASE1_DB_TRUST_SERVER_CERT", "yes"))
    report = AcceptanceRunner(args.api_url, args.expected_server, args.expected_database, sql, credentials).run()
    write_report(report, Path(args.report))
    print(report.status)
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
