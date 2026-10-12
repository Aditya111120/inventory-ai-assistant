"""Server-side authentication and role scope resolution for Phase 1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from app.context import UserContext


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: int
    username: str
    display_name: str
    role_code: str


@dataclass(frozen=True)
class AuthorizedScope:
    user: AuthenticatedUser
    allowed_store_ids: tuple[int, ...] | None
    allowed_product_ids: tuple[int, ...] | None
    allowed_regions: tuple[str, ...] = ()
    allowed_brands: tuple[str, ...] = ()

    def context(self) -> UserContext:
        return UserContext(
            user_id=str(self.user.user_id),
            display_name=self.user.display_name,
            persona={
                "STORE_MANAGER": "Store Manager",
                "BRAND_MANAGER": "Brand Manager",
                "PLANNER": "Planner",
                "LEADERSHIP": "Leadership",
            }.get(self.user.role_code, "Planner"),
            authorized_store_ids=self.allowed_store_ids,
            authorized_product_ids=self.allowed_product_ids,
            # Empty tuples are intentional fail-closed scopes; do not turn
            # them into None (which means unrestricted).
            authorized_brands=self.allowed_brands,
            authorized_regions=self.allowed_regions,
        )


class AuthService:
    """SQL-backed login/session service. Only hashes, never raw tokens, persist."""

    ph = PasswordHasher()

    def __init__(
        self,
        conn,
        *,
        session_ttl_minutes: int = 480,
        demo_only: bool = False,
        demo_account: str = "LD-01",
    ):
        self.conn = conn
        self.session_ttl = timedelta(minutes=session_ttl_minutes)
        self.demo_only = bool(demo_only)
        self.demo_account = str(demo_account or "LD-01").strip()

    def _demo_allowed(self, username: str) -> bool:
        return not self.demo_only or str(username).casefold() == self.demo_account.casefold()

    def _one(self, sql: str, params=()):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            columns = [d[0] for d in cur.description]
            return dict(zip(columns, row))
        finally:
            cur.close()

    def _all(self, sql: str, params=()):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    def login(self, username: str, password: str) -> tuple[AuthenticatedUser, str]:
        row = self._one(
            "SELECT u.user_id,u.username,u.display_name,u.password_hash,u.is_active,r.role_code "
            "FROM dbo.AppUsers u JOIN dbo.AppRoles r ON r.role_id=u.role_id WHERE u.username=?",
            [username],
        )
        if not row or not row["is_active"] or not row.get("password_hash"):
            raise PermissionError("Invalid username or password")
        if not self._demo_allowed(row["username"]):
            raise PermissionError("This demo is currently available to Leadership only.")
        try:
            self.ph.verify(str(row["password_hash"]), password)
        except (VerifyMismatchError, VerificationError, ValueError) as exc:
            raise PermissionError("Invalid username or password") from exc
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        expires = datetime.now(timezone.utc) + self.session_ttl
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO dbo.AppSessions(token_hash,user_id,expires_at,revoked_at) VALUES(?,?,?,NULL)",
                [digest, row["user_id"], expires.replace(tzinfo=None)],
            )
            self.conn.commit()
        finally:
            cur.close()
        return AuthenticatedUser(int(row["user_id"]), row["username"], row["display_name"], row["role_code"]), token

    def user_for_token(self, token: str | None) -> AuthenticatedUser | None:
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        row = self._one(
            "SELECT u.user_id,u.username,u.display_name,r.role_code FROM dbo.AppSessions s "
            "JOIN dbo.AppUsers u ON u.user_id=s.user_id JOIN dbo.AppRoles r ON r.role_id=u.role_id "
            "WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>SYSUTCDATETIME() AND u.is_active=1",
            [digest],
        )
        if not row or not self._demo_allowed(row["username"]):
            return None
        return AuthenticatedUser(int(row["user_id"]), row["username"], row["display_name"], row["role_code"])

    def logout(self, token: str | None) -> None:
        if not token:
            return
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        cur = self.conn.cursor()
        try:
            cur.execute("UPDATE dbo.AppSessions SET revoked_at=SYSUTCDATETIME() WHERE token_hash=?", [digest])
            self.conn.commit()
        finally:
            cur.close()

    def scope(self, user: AuthenticatedUser) -> AuthorizedScope:
        if user.role_code in {"PLANNER", "LEADERSHIP"}:
            return AuthorizedScope(user, None, None)
        stores = None
        products = None
        regions: tuple[str, ...] = ()
        brands: tuple[str, ...] = ()
        if user.role_code == "STORE_MANAGER":
            region_rows = self._all(
                "SELECT region FROM dbo.UserRegionAccess WHERE user_id=? ORDER BY region",
                [user.user_id],
            )
            regions = tuple(str(r["region"]) for r in region_rows)
            rows = self._all(
                "SELECT s.store_id FROM dbo.Stores s JOIN dbo.UserRegionAccess a ON a.region=s.region WHERE a.user_id=?",
                [user.user_id],
            )
            stores = tuple(sorted(int(r["store_id"]) for r in rows))
        elif user.role_code == "BRAND_MANAGER":
            brand_rows = self._all(
                "SELECT brand FROM dbo.UserBrandAccess WHERE user_id=? ORDER BY brand",
                [user.user_id],
            )
            brands = tuple(str(r["brand"]) for r in brand_rows)
            rows = self._all(
                "SELECT p.product_id FROM dbo.Products p JOIN dbo.UserBrandAccess a ON a.brand=p.brand WHERE a.user_id=? AND p.is_active=1",
                [user.user_id],
            )
            products = tuple(sorted(int(r["product_id"]) for r in rows))
        return AuthorizedScope(user, stores, products, regions, brands)

