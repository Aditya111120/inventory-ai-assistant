from __future__ import annotations

import json
from pathlib import Path
import sys
import types
import pytest

from scripts.phase1_acceptance_runner import (
    ACCOUNTS,
    AcceptanceRunner,
    ReadOnlySql,
    RunReport,
    write_report,
)
from scripts.leadership_acceptance_runner import EXCLUDED_ACCOUNTS, MATRIX as LD_MATRIX, Runner as LeadershipRunner, ReadOnlySql as LeadershipSql, _below_reorder_predicate, _response_counts


def test_all_eight_accounts_are_required():
    assert ACCOUNTS == ("SM-01", "SM-02", "SM-03", "SM-04", "BM-01", "BM-02", "PL-01", "LD-01")


def test_leadership_matrix_is_ld01_only_and_covers_required_capabilities():
    names = {item[0] for item in LD_MATRIX}
    assert names == {
        "inventory_totals", "inventory_value", "category_ranking", "brand_ranking",
        "store_revenue", "sales_units", "sales_by_category", "sales_by_brand",
        "sales_by_city", "risk", "zero_stock", "historical", "comparison",
        "period_comparison", "what_if", "executive_summary", "clarification", "context_isolation",
    }
    runner = LeadershipRunner("http://127.0.0.1", LeadershipSql("server", "InventoryAI_Test"), "not-printed")
    assert runner.password == "not-printed"
    assert "SM-01" not in str(runner.report.as_dict())
    assert set(EXCLUDED_ACCOUNTS) == {"SM-01", "SM-02", "SM-03", "SM-04", "BM-01", "BM-02", "PL-01"}


def test_period_comparison_acceptance_compares_both_full_months_and_delta():
    reference = {
        "available": True,
        "periods": ((6, 2026), (5, 2026)),
        "expected_params": [["2026-05-31", "2026-06-30"], ["2026-04-30", "2026-05-31"]],
        "rows": [{"group_name": "Group A", "units": 120, "units_prev": 100}],
    }
    payload = {
        "answer_facts": {"time_scope": {"kind": "month", "month": 6, "year": 2026, "comparison_month": 5, "comparison_year": 2026}},
        "trust_panel": {"query": {"params": reference["expected_params"]}},
        "response": {"all_rows": [{
            "category": "Group A", "units": 120, "units_prev": 100, "units_change": 20,
        }]},
    }
    LeadershipRunner._assert_numeric("period_comparison", payload, reference)


def test_period_comparison_acceptance_rejects_unreconciled_delta():
    import pytest

    reference = {"available": True, "rows": [{"group_name": "Group A", "units": 120, "units_prev": 100}]}
    payload = {"response": {"all_rows": [{
        "category": "Group A", "units": 120, "units_prev": 100, "units_change": 19,
    }]}}
    with pytest.raises(AssertionError, match="period delta mismatch"):
        LeadershipRunner._assert_numeric("period_comparison", payload, reference)


def test_period_comparison_acceptance_rejects_wrong_effective_sql_windows():
    import pytest

    reference = {
        "available": True,
        "periods": ((6, 2026), (5, 2026)),
        "expected_params": [["2026-05-31", "2026-06-30"], ["2026-04-30", "2026-05-31"]],
        "rows": [{"group_name": "Group A", "units": 120, "units_prev": 100}],
    }
    payload = {
        "answer_facts": {"time_scope": {"kind": "month", "month": 6, "year": 2026, "comparison_month": 5, "comparison_year": 2026}},
        "trust_panel": {"query": {"params": [["2026-06-01", "2026-06-30"], ["2026-05-01", "2026-05-31"]]}},
        "response": {"all_rows": [{
            "category": "Group A", "units": 120, "units_prev": 100, "units_change": 20,
        }]},
    }
    with pytest.raises(AssertionError, match="effective SQL period parameters differ"):
        LeadershipRunner._assert_numeric("period_comparison", payload, reference)


def test_clarification_response_with_null_answer_facts_is_safe():
    payload = {"status": "clarification", "answer_facts": None,
               "response": {"answer_facts": None, "scope": None, "result_counts": None}}
    assert _response_counts(payload) == {}


def test_leadership_runner_reports_api_clarification_before_identity_assertions():
    payload = {"status": "clarification", "error": "Which store?", "table": []}
    with pytest.raises(AssertionError, match="status='clarification'.*Which store"):
        LeadershipRunner._assert_numeric("what_if", payload, {})


def test_sql_reference_below_reorder_predicate_matches_v1_boundaries():
    predicate = _below_reorder_predicate("i")
    assert "i.quantity_on_hand>0" in predicate  # zero is V1 Out of Stock
    assert "i.reorder_level>=0" in predicate
    assert "i.max_level IS NOT NULL" in predicate  # missing max remains eligible as V1 does
    assert "i.max_level<i.reorder_level" in predicate  # invalid configuration is excluded
    assert "i.quantity_on_hand<=i.reorder_level" in predicate


@pytest.mark.parametrize("demand_change_pct", [10, 20, 30, 50, -10])
def test_what_if_acceptance_reads_nested_result_rows_and_checks_resolved_ids(demand_change_pct):
    factor = max(1 + demand_change_pct / 100, 0)
    actual = 60
    simulated = actual / factor
    reference = {
        "product_id": 311, "store_id": 7, "product_name": "Example Earbuds",
        "store_name": "Example Store", "quantity_on_hand": 120,
        "units_sold": 60, "window_days": 30, "demand_change_pct": demand_change_pct,
    }
    payload = {
        "intent": {"scenario": {"kind": "demand_change_pct", "value": demand_change_pct}},
        "basis": "Simulated",
        "resolved_context": {
            "product_scope": {"product_ids": [311]},
            "location_scope": {"store_ids": [7]},
        },
        "trust_panel": {
            "query": {"params": [["2026-08-31", "2026-09-30", 311, 7]]},
            "calculations": [
                {"metric": "what_if_days_of_inventory", "result": simulated},
                {"metric": "what_if_comparison", "result": {
                    "actual_days_of_inventory": actual,
                    "simulated_days_of_inventory": simulated,
                }},
            ],
        },
        "response": {"all_rows": [{
            "product_name": "Example Earbuds", "store_name": "Example Store",
            "actual_days_of_inventory": actual, "simulated_days_of_inventory": simulated,
        }]},
    }
    LeadershipRunner._assert_numeric("what_if", payload, reference)


def test_what_if_acceptance_rejects_wrong_resolved_product_or_store():
    import pytest

    reference = {
        "product_id": 311, "store_id": 7, "product_name": "Example Earbuds",
        "store_name": "Example Store", "quantity_on_hand": 120,
        "units_sold": 60, "window_days": 30,
    }
    payload = {
        "basis": "Simulated",
        "resolved_context": {
            "product_scope": {"product_ids": [312]},
            "location_scope": {"store_ids": [7]},
        },
        "trust_panel": {"calculations": [{"metric": "what_if_days_of_inventory", "result": 50}]},
        "table": [{"product_name": "Example Earbuds", "store_name": "Example Store",
                   "actual_days_of_inventory": 60, "simulated_days_of_inventory": 50}],
    }
    with pytest.raises(AssertionError, match="effective product/store IDs"):
        LeadershipRunner._assert_numeric("what_if", payload, reference)


def test_what_if_acceptance_requires_store_identity_in_result_rows():
    import pytest

    reference = {
        "product_id": 311, "store_id": 7, "product_name": "Example Earbuds",
        "store_name": "Example Store", "quantity_on_hand": 120,
        "units_sold": 60, "window_days": 30, "demand_change_pct": 20,
    }
    payload = {
        "intent": {"scenario": {"kind": "demand_change_pct", "value": 20}},
        "basis": "Simulated",
        "resolved_context": {
            "product_scope": {"product_ids": [311]},
            "location_scope": {"store_ids": [7]},
        },
        "trust_panel": {"calculations": []},
        "response": {"all_rows": [{
            "product_name": "Example Earbuds", "quantity_on_hand": 120,
            "actual_days_of_inventory": 60, "simulated_days_of_inventory": 50,
        }]},
    }
    with pytest.raises(AssertionError, match="did not retain the SQL-selected product/store"):
        LeadershipRunner._assert_numeric("what_if", payload, reference)


def test_ld01_matrix_production_guard_blocks_before_connection(monkeypatch):
    monkeypatch.delenv("PHASE1_ALLOW_PRODUCTION", raising=False)
    sql = LeadershipSql("server", "InventoryAI")
    runner = LeadershipRunner("http://127.0.0.1", sql, "secret")
    report = runner.run()
    assert report.status == "BLOCKED"
    assert report.cases
    assert all(case.status == "BLOCKED" for case in report.cases)
    assert "secret" not in json.dumps(report.as_dict())


def test_credentials_are_read_from_environment_mapping(monkeypatch):
    monkeypatch.setenv("PHASE1_PASSWORD_SM_01", "secret")
    creds = AcceptanceRunner.credentials_from_env()
    assert creds["SM-01"] == "secret"
    # Credentials are not included in report structures.
    assert "secret" not in json.dumps(RunReport().as_dict())


def test_protected_database_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("PHASE1_ALLOW_PRODUCTION", raising=False)
    sql = ReadOnlySql("server", "InventoryAI", "driver", "optional", "yes")
    runner = AcceptanceRunner("http://127.0.0.1:9", "server", "InventoryAI", sql, {})
    report = runner.run()
    assert report.status == "BLOCKED"
    assert any("Protected InventoryAI" in item for item in report.blockers)


def test_runner_defaults_to_documented_sql_auth_without_printing_secret(monkeypatch):
    captured = {}

    def connect(connection_string, **kwargs):
        captured["connection_string"] = connection_string
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setitem(sys.modules, "pyodbc", types.SimpleNamespace(connect=connect))
    monkeypatch.delenv("PHASE1_DB_AUTH", raising=False)
    monkeypatch.delenv("DB_AUTH", raising=False)
    monkeypatch.setenv("PHASE1_DB_USER", "integration_user")
    monkeypatch.setenv("PHASE1_DB_PASSWORD", "not-printed")
    sql = ReadOnlySql("server", "InventoryAI_Test", "driver", "optional", "yes")

    sql.connect()

    assert "Trusted_Connection=yes" not in captured["connection_string"]
    assert "UID=integration_user" in captured["connection_string"]
    assert "PWD=not-printed" in captured["connection_string"]
    assert captured["kwargs"]["autocommit"] is True


def test_report_is_reviewable_and_does_not_create_history():
    report = RunReport(status="BLOCKED", target={"server": "s", "database": "InventoryAI_Test"})
    payload = report.as_dict()
    assert payload["status"] == "BLOCKED"
    assert "sqlite3" not in json.dumps(payload).casefold()


def test_authorization_checker_rejects_rows_outside_scope():
    runner = AcceptanceRunner("http://example", "s", "d", object(), {})
    payload = {"display_rows": [{"brand": "Samsung", "region": "North"}]}
    scopes = {"BM-02": {"brands": ["HP"], "regions": []}}
    stores = []
    try:
        runner._assert_authorized(payload, "BM-02", scopes, stores)
    except AssertionError as exc:
        assert "unauthorized brand" in str(exc)
    else:
        raise AssertionError("unauthorized row was accepted")
