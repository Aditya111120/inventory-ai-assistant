from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from app.api import create_app
from app.auth import AuthenticatedUser, AuthorizedScope


class ExploreAuthService:
    def __init__(self, store_ids=(2,), product_ids=(4, 8), brands=()):
        self.conn = None
        self.used_connections = []
        self._lock = Lock()
        self.user = AuthenticatedUser(7, "SM-01", "North Manager", "STORE_MANAGER")
        self.authorized_scope = AuthorizedScope(
            self.user,
            store_ids,
            product_ids,
            ("North",),
            brands,
        )

    def user_for_token(self, token):
        if self.conn is not None:
            with self._lock:
                self.used_connections.append(self.conn)
        return self.user if token == "valid-token" else None

    def scope(self, user):
        return self.authorized_scope


class ExploreQueryRunner:
    s = "dbo."
    conn = object()

    def __init__(self, barrier=None):
        self.calls = []
        self.barrier = barrier
        self.seen_connections = set()
        self._lock = Lock()

    def run(self, conn, sql, params):
        if self.barrier is not None:
            with self._lock:
                first_call = id(conn) not in self.seen_connections
                self.seen_connections.add(id(conn))
            if first_call:
                self.barrier.wait(timeout=5)
        self.calls.append((sql, list(params)))
        if "1 = 0" in sql:
            if "COUNT_BIG(*)" in sql:
                return pd.DataFrame({"total": [0]})
            return pd.DataFrame(columns=[
                "product_id", "sku", "product_name", "brand", "category",
                "subcategory", "is_active",
            ])
        if "COUNT_BIG(*) AS total" in sql and "ProductSubcategories" in sql:
            return pd.DataFrame({"total": [61]})
        if "COUNT_BIG(*) AS total" in sql:
            return pd.DataFrame({"total": [2]})
        if "SELECT p.product_id, p.sku" in sql:
            return pd.DataFrame([{
                "product_id": 8,
                "sku": "SKU-8",
                "product_name": "Product Eight",
                "brand": "Example",
                "category": "Hardware",
                "subcategory": None,
                "is_active": True,
            }])
        if "SELECT DISTINCT p.brand" in sql:
            return pd.DataFrame({
                "brand": ["Example"],
                "category": ["Hardware"],
                "subcategory": ["Accessories"],
            })
        if sql.startswith("SELECT st.store_id"):
            return pd.DataFrame([{
                "store_id": 2,
                "store_name": "North Store",
                "city": "Sample City",
                "region": "North",
            }])
        if "COUNT(DISTINCT CASE WHEN" in sql:
            return pd.DataFrame({
                "inventory_products": [1],
                "positive_stock_products": [1],
            })
        if "MIN(" in sql and "MAX(" in sql:
            return pd.DataFrame({
                "first_date": ["2026-01-01"],
                "last_date": ["2026-02-01"],
            })
        return pd.DataFrame({"total": [2]})


class TrackedConnection:
    def __init__(self, connection_id, all_connections):
        self.connection_id = connection_id
        self.all_connections = all_connections
        self.closed = False

    def close(self):
        self.closed = True


class ConnectionFactory:
    def __init__(self):
        self.connections = []
        self._lock = Lock()

    def __call__(self):
        with self._lock:
            connection = TrackedConnection(len(self.connections), self.connections)
            self.connections.append(connection)
            return connection


class ConcurrentChatOrchestrator:
    def __init__(self, auth_service, queries):
        self.auth_service = auth_service
        self.queries = queries

    def handle(self, *, question, session_id):
        return SimpleNamespace(
            session_id=session_id,
            status="ok",
            intent=None,
            narrative="Concurrent request completed.",
            data=pd.DataFrame(),
            interpretation=[],
            trust_panel={},
            basis="Actual",
            usage=None,
            error=None,
            narration_verified=True,
            narration_regenerated=False,
            narration_fallback=False,
            followups=[],
            result_counts={},
            clarification_kind=None,
            clarification_prompt=None,
            clarification_options=[],
            pending_clarification_id=None,
            resolved_context={},
            answer_facts=None,
            user_context={},
            response_type="lookup",
            display={},
        )


def make_client(
    *,
    store_ids=(2,),
    product_ids=(4, 8),
    brands=(),
    runner=None,
    connection_factory=None,
    raise_server_exceptions=True,
):
    runner = runner or ExploreQueryRunner()
    auth = ExploreAuthService(store_ids, product_ids, brands)
    orchestrator = SimpleNamespace(
        auth_service=auth,
        queries=runner,
    )
    client = TestClient(
        create_app(lambda: orchestrator, connection_factory=connection_factory),
        raise_server_exceptions=raise_server_exceptions,
    )
    client.cookies.set("inventory_session", "valid-token")
    return client, runner


def test_explore_requires_an_authenticated_session():
    client, _ = make_client()
    client.cookies.clear()

    assert client.get("/explore/products").status_code == 401


def test_product_search_filters_and_pagination_are_parameterized_and_scoped():
    client, runner = make_client()

    response = client.get(
        "/explore/products",
        params={
            "search": "SKU",
            "brand": "Example",
            "category": "Hardware",
            "subcategory": "Accessories",
            "page": 3,
            "page_size": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 61
    assert response.json()["items"][0]["sku"] == "SKU-8"
    sql, params = runner.calls[-1]
    assert "p.product_id IN (?,?)" in sql
    assert "p.sku" in sql and "LOWER(p.category) = ?" in sql
    assert "ps.subcategory_name) = ?" in sql
    assert params == [4, 8, "%sku%", "%sku%", "%sku%", "example", "hardware", "accessories", 40, 20]


def test_locations_are_limited_to_authorized_store_ids():
    client, runner = make_client(store_ids=(2,))

    response = client.get("/explore/locations")

    assert response.status_code == 200
    assert [item["store_id"] for item in response.json()["items"]] == [2]
    sql, params = runner.calls[-1]
    assert "st.store_id IN (?)" in sql
    assert params == [2]


def test_empty_product_scope_fails_closed_for_product_catalog():
    client, runner = make_client(product_ids=())

    response = client.get("/explore/products")

    assert response.status_code == 200
    assert response.json()["items"] == []
    count_sql, count_params = runner.calls[-2]
    assert "1 = 0" in count_sql
    assert count_params == []


def test_availability_uses_product_and_store_authorization_for_all_coverage():
    client, runner = make_client(store_ids=(2,), product_ids=(4, 8))

    response = client.get("/explore/availability")

    assert response.status_code == 200
    body = response.json()
    assert body["catalog_products"] == 2
    assert body["inventory_products"] == 1
    assert body["positive_stock_products"] == 1
    assert body["coverage"]["sales"] == {
        "first_date": "2026-01-01",
        "last_date": "2026-02-01",
    }
    coverage_calls = [
        (sql, params)
        for sql, params in runner.calls
        if "MIN(" in sql and "MAX(" in sql
    ]
    assert len(coverage_calls) == 3
    assert all("p.product_id IN (?,?)" in sql and "st.store_id IN (?)" in sql for sql, _ in coverage_calls)
    assert all(params == [4, 8, 2] for _, params in coverage_calls)


def test_concurrent_explore_requests_use_distinct_connections_and_close_them():
    barrier = Barrier(4)
    runner = ExploreQueryRunner(barrier=barrier)
    connection_factory = ConnectionFactory()
    orchestrator = SimpleNamespace(
        auth_service=ExploreAuthService(),
        queries=runner,
    )
    app = create_app(lambda: orchestrator, connection_factory=connection_factory)

    def get(path):
        with TestClient(app) as client:
            client.cookies.set("inventory_session", "valid-token")
            return client.get(path)

    paths = (
        "/explore/products",
        "/explore/filters",
        "/explore/locations",
        "/explore/availability",
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(get, paths))

    assert [response.status_code for response in responses] == [200] * 4
    assert responses[0].json()["items"][0]["sku"] == "SKU-8"
    assert responses[1].json()["brands"] == ["Example"]
    assert responses[2].json()["items"][0]["store_id"] == 2
    assert responses[3].json()["catalog_products"] == 2
    assert len(connection_factory.connections) == 4
    assert len(runner.calls) > len(connection_factory.connections)
    assert len({id(conn) for conn in runner.seen_connections}) == 4
    assert {id(conn) for conn in orchestrator.auth_service.used_connections} == {
        id(conn) for conn in connection_factory.connections
    }
    assert all(connection.closed for connection in connection_factory.connections)


def test_request_connection_is_closed_when_endpoint_raises():
    connection_factory = ConnectionFactory()
    runner = ExploreQueryRunner()

    def fail_query(conn, sql, params):
        raise RuntimeError("query failed")

    runner.run = fail_query
    client, _ = make_client(
        runner=runner,
        connection_factory=connection_factory,
        raise_server_exceptions=False,
    )

    response = client.get("/explore/products")

    assert response.status_code == 500
    assert len(connection_factory.connections) == 1
    assert connection_factory.connections[0].closed


def test_concurrent_chat_auth_and_explore_requests_close_request_connections():
    runner = ExploreQueryRunner(barrier=Barrier(2))
    connection_factory = ConnectionFactory()
    auth = ExploreAuthService()
    orchestrator = ConcurrentChatOrchestrator(auth, runner)
    app = create_app(lambda: orchestrator, connection_factory=connection_factory)

    def request(method, path, **kwargs):
        with TestClient(app) as client:
            client.cookies.set("inventory_session", "valid-token")
            if method == "post":
                return client.post(path, json=kwargs["json"])
            return client.get(path)

    calls = [
        ("get", "/auth/me", {}),
        ("post", "/chat", {"json": {"question": "show inventory"}}),
        ("get", "/explore/products", {}),
        ("get", "/explore/filters", {}),
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(
            executor.map(
                lambda call: request(call[0], call[1], **call[2]),
                calls,
            )
        )

    assert [response.status_code for response in responses] == [200] * 4
    assert responses[0].json()["account_id"] == "SM-01"
    assert responses[1].json()["narrative"] == "Concurrent request completed."
    assert len(connection_factory.connections) == 4
    assert {id(conn) for conn in auth.used_connections} == {
        id(conn) for conn in connection_factory.connections
    }
    assert all(connection.closed for connection in connection_factory.connections)
    assert len(runner.seen_connections) == 2
