import pytest

from app.auth import AuthenticatedUser, AuthorizedScope, AuthService
from app.context import role_policy


def test_role_scope_maps_to_server_owned_context():
    user = AuthenticatedUser(7, "SM-01", "North Manager", "STORE_MANAGER")
    context = AuthorizedScope(user, (1, 2), None).context()
    assert context.user_id == "7"
    assert context.persona == "Store Manager"
    assert context.authorized_store_ids == (1, 2)
    assert context.authorized_product_ids is None


def test_empty_scope_fails_closed_in_context():
    user = AuthenticatedUser(8, "BM-01", "Brand Manager", "BRAND_MANAGER")
    context = AuthorizedScope(user, (), ()).context()
    assert context.authorized_store_ids == ()
    assert context.authorized_product_ids == ()


def test_leadership_only_demo_gate_is_reversible_and_server_side():
    service = AuthService(object(), demo_only=True, demo_account="LD-01")
    assert service._demo_allowed("LD-01")
    assert not service._demo_allowed("BM-01")
    disabled = AuthService(object(), demo_only=False)
    assert disabled._demo_allowed("BM-01")


class _AuthCursor:
    description = [("user_id",), ("username",), ("display_name",), ("password_hash",), ("is_active",), ("role_code",)]
    row = (2, "BM-01", "Brand", "hash", 1, "BRAND_MANAGER")

    def execute(self, sql, params=()):
        return self

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def close(self):
        pass


class _AuthConn:
    def cursor(self):
        return _AuthCursor()

    def commit(self):
        pass


def test_leadership_only_gate_blocks_login_before_password_verification():
    service = AuthService(_AuthConn(), demo_only=True)
    with pytest.raises(PermissionError, match="Leadership only"):
        service.login("BM-01", "not-a-password")


@pytest.mark.parametrize(
    ("username", "role"),
    [
        ("SM-01", "STORE_MANAGER"), ("SM-02", "STORE_MANAGER"),
        ("SM-03", "STORE_MANAGER"), ("SM-04", "STORE_MANAGER"),
        ("BM-01", "BRAND_MANAGER"), ("BM-02", "BRAND_MANAGER"),
        ("PL-01", "PLANNER"), ("LD-01", "LEADERSHIP"),
    ],
)
def test_all_phase_one_accounts_map_to_a_reusable_role_policy(username, role):
    user = AuthenticatedUser(1, username, username, role)
    persona = AuthorizedScope(user, (1,) if role == "STORE_MANAGER" else None, (2,) if role == "BRAND_MANAGER" else None).context().persona
    policy = role_policy(persona)
    assert policy["focus"]
    assert policy["priority"]
