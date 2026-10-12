from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import migration_runner as runner


def test_dry_run_does_not_connect(monkeypatch, capsys):
    monkeypatch.setenv("DB_SERVER", "test-server")
    monkeypatch.setenv("DB_NAME", "InventoryAI")
    monkeypatch.setenv("DB_AUTH", "windows")
    monkeypatch.setattr(runner.pyodbc, "connect", lambda *a, **k: pytest.fail("dry run connected"))
    assert runner.main([]) == 0
    assert "No database connection opened" in capsys.readouterr().out


def test_sql_auth_requires_secret(monkeypatch):
    monkeypatch.setenv("DB_SERVER", "test-server")
    monkeypatch.setenv("DB_AUTH", "sql")
    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    with pytest.raises(runner.RunnerError, match="DB_USER"):
        runner.env_settings()


def test_encryption_setting_is_explicit_and_configurable(monkeypatch):
    monkeypatch.setenv("DB_SERVER", "test-server")
    monkeypatch.setenv("DB_AUTH", "windows")
    monkeypatch.setenv("DB_ENCRYPT", "optional")
    cfg = runner.env_settings()
    assert cfg["encrypt"] == "optional"
    assert "Encrypt=optional" in runner.connection_string(cfg)


def test_invalid_encryption_setting_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_SERVER", "test-server")
    monkeypatch.setenv("DB_AUTH", "windows")
    monkeypatch.setenv("DB_ENCRYPT", "sometimes")
    with pytest.raises(runner.RunnerError, match="DB_ENCRYPT"):
        runner.env_settings()


def test_loader_has_no_active_assignments():
    assert runner.active_approved_assignments() is False


def test_compare_validation_detects_fingerprint_change():
    pre = {"result_sets": [{"columns": ["product_id_sku_sha256"], "rows": [{"product_id_sku_sha256": "aa"}]}]}
    post = {"result_sets": [{"columns": ["product_id_sku_sha256"], "rows": [{"product_id_sku_sha256": "bb"}]}]}
    assert runner.compare_validation(pre, post)


def test_report_writer_does_not_include_connection_string():
    destination = Path(".runner-test-report.json")
    try:
        runner.write_report(destination, {"target": "server/InventoryAI (sql authentication)"})
        assert "PWD=" not in destination.read_text(encoding="utf-8")
    finally:
        destination.unlink(missing_ok=True)


def test_go_batches_are_refused():
    sql_file = Path(".runner-batched.sql")
    try:
        sql_file.write_text("SELECT 1;\nGO\nSELECT 2;", encoding="utf-8")
        with pytest.raises(runner.RunnerError, match="contains GO"):
            runner.run_sql(object(), sql_file)
    finally:
        sql_file.unlink(missing_ok=True)


def test_sql_execution_error_is_propagated_and_cursor_closed():
    class FailingCursor:
        def execute(self, _sql):
            raise RuntimeError("simulated SQL failure")

        def close(self):
            self.closed = True

    class Connection:
        def cursor(self):
            return FailingCursor()

    sql_file = Path(".runner-failing.sql")
    try:
        sql_file.write_text("SELECT 1;", encoding="utf-8")
        with pytest.raises(RuntimeError, match="simulated SQL failure"):
            runner.run_sql(Connection(), sql_file)
    finally:
        sql_file.unlink(missing_ok=True)


def test_apply_requires_exact_target_arguments(monkeypatch, capsys):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI")
    monkeypatch.setenv("DB_AUTH", "windows")
    assert runner.main(["--apply", "--recovery-plan", "missing-recovery.md"]) == 1
    assert "expected-server" in capsys.readouterr().err


def test_mismatched_target_stops_before_connect(monkeypatch, capsys):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI")
    monkeypatch.setenv("DB_AUTH", "windows")
    monkeypatch.setattr(runner.pyodbc, "connect", lambda *a, **k: pytest.fail("wrong target connected"))
    assert runner.main(["--expected-server", "server-b"]) == 1
    assert "does not match" in capsys.readouterr().err


def test_database_identity_mismatch_is_rejected():
    result = [{"columns": ["database_name", "server_name", "sql_server_version"],
               "rows": [{"database_name": "OtherDb", "server_name": "server-a"}]}]
    with pytest.raises(runner.RunnerError, match="database identity"):
        runner.verify_database_identity(result, {"database": "InventoryAI"})


def test_missing_database_identity_is_rejected():
    with pytest.raises(runner.RunnerError, match="did not return"):
        runner.verify_database_identity([], {"database": "InventoryAI"})


def test_preflight_never_invokes_migration_sql(monkeypatch):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI_Test")
    monkeypatch.setenv("DB_AUTH", "windows")
    monkeypatch.setenv("DB_ENCRYPT", "optional")

    class Connection:
        autocommit = False
        def __enter__(self): return self
        def __exit__(self, *_): return False

    monkeypatch.setattr(runner.pyodbc, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(runner, "run_sql", lambda *a, **k: pytest.fail("migration SQL invoked"))
    monkeypatch.setattr(runner, "execute_query", lambda conn, sql: [
        {"columns": ["database_name", "server_name", "sql_server_version"],
         "rows": [{"database_name": "InventoryAI_Test", "server_name": "server-a"}]},
        {"columns": ["table_name", "row_count"], "rows": [{"table_name": "Products", "row_count": 1}]},
    ])
    destination = Path(".runner-preflight-report.json")
    try:
        assert runner.main(["--preflight", "--expected-server", "server-a",
                            "--expected-database", "InventoryAI_Test", "--report", str(destination)]) == 0
        assert '"database_changes": false' in destination.read_text(encoding="utf-8")
    finally:
        destination.unlink(missing_ok=True)


def test_preflight_rejects_protected_database(monkeypatch):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI")
    monkeypatch.setenv("DB_AUTH", "windows")
    monkeypatch.setattr(runner.pyodbc, "connect", lambda *a, **k: pytest.fail("protected database connected"))
    assert runner.main(["--preflight", "--expected-server", "server-a",
                        "--expected-database", "InventoryAI_Test"]) == 1


def test_preflight_accepts_pre_migration_results_without_subcategory():
    assert runner.preflight_failures([{
        "columns": ["table_name", "row_count"],
        "rows": [{"table_name": "Products", "row_count": 150}],
    }]) == []


def test_preflight_detects_post_migration_constraint_violation():
    failures = runner.preflight_failures([{
        "columns": ["product_id", "sku", "category", "subcategory_id"],
        "rows": [{"product_id": 1, "sku": "SKU-1", "category": "Audio", "subcategory_id": 4}],
    }])
    assert "Subcategory constraint violations detected" in failures


def test_validation_subcategory_checks_are_dynamic_and_read_only():
    sql = runner.VALIDATION.read_text(encoding="utf-8-sig")
    assert "sp_executesql" in sql
    assert runner.read_only_sql_is_safe(sql)


def test_preflight_reports_sql_compilation_error(monkeypatch):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI_Test")
    monkeypatch.setenv("DB_AUTH", "windows")

    class Connection:
        autocommit = False
        def __enter__(self): return self
        def __exit__(self, *_): return False

    monkeypatch.setattr(runner.pyodbc, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(runner, "execute_query", lambda *a: (_ for _ in ()).throw(
        runner.pyodbc.ProgrammingError("Invalid column name 'subcategory_id' (207)")))
    destination = Path(".runner-compile-error.json")
    try:
        assert runner.main(["--preflight", "--expected-server", "server-a",
                            "--expected-database", "InventoryAI_Test", "--report", str(destination)]) == 1
        assert "subcategory_id" in destination.read_text(encoding="utf-8")
    finally:
        destination.unlink(missing_ok=True)


def test_schema_migration_defers_new_column_references_to_dynamic_sql():
    sql = runner.SCHEMA.read_text(encoding="utf-8-sig")
    assert "EXEC sys.sp_executesql N'ALTER TABLE dbo.Products ADD subcategory_id SMALLINT NULL;'" in sql
    assert "EXEC sys.sp_executesql N'" in sql
    assert "BEGIN TRY" in sql and "IF XACT_STATE() <> 0 ROLLBACK TRANSACTION" in sql
    assert "ALTER TABLE dbo.Products WITH CHECK" in sql
    assert "CREATE INDEX IX_Products_subcategory_id" in sql


def test_classification_loader_stays_empty_and_rejects_unapproved_existing_assignments():
    sql = runner.LOADER.read_text(encoding="utf-8-sig")
    assert runner.active_approved_assignments() is False
    assert "Unexpected existing subcategory assignments detected" in sql
    assert "BEGIN TRANSACTION" in sql and "ROLLBACK TRANSACTION" in sql


def test_production_apply_requires_separate_authorization(monkeypatch, capsys):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI")
    monkeypatch.setenv("DB_AUTH", "windows")
    assert runner.main(["--apply", "--expected-server", "server-a",
                        "--expected-database", "InventoryAI",
                        "--recovery-plan", "missing-recovery.md"]) == 1
    assert "production-authorize" in capsys.readouterr().err


def test_production_authorization_cannot_target_test(monkeypatch, capsys):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI_Test")
    monkeypatch.setenv("DB_AUTH", "windows")
    assert runner.main(["--apply", "--production-authorize",
                        "--expected-server", "server-a",
                        "--expected-database", "InventoryAI_Test",
                        "--recovery-plan", "missing-recovery.md"]) == 1
    assert "only when DB_NAME" in capsys.readouterr().err


def test_production_preflight_rejects_test_target(monkeypatch, capsys):
    monkeypatch.setenv("DB_SERVER", "server-a")
    monkeypatch.setenv("DB_NAME", "InventoryAI_Test")
    monkeypatch.setenv("DB_AUTH", "windows")
    monkeypatch.setattr(runner.pyodbc, "connect", lambda *a, **k: pytest.fail("wrong target connected"))
    assert runner.main(["--production-preflight", "--expected-server", "server-a",
                        "--expected-database", "InventoryAI_Test"]) == 1
    assert "restricted" in capsys.readouterr().err
