import pytest

from app.config import ConfigError, load_settings


def _env(monkeypatch, **kw):
    for k in (
        "DB_SERVER",
        "DB_NAME",
        "DB_AUTH",
        "DB_USER",
        "DB_PASSWORD",
        "DB_DRIVER",
        "DB_TRUST_SERVER_CERT",
        "GROQ_API_KEY",
        "GROQ_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)
    base = {"DB_SERVER": r"HOST\SQLEXPRESS01", "DB_USER": "u", "DB_PASSWORD": "pw"}
    base.update(kw)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


def test_sql_auth_connection_string(monkeypatch):
    _env(monkeypatch)
    cs = load_settings().connection_string()
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in cs
    assert r"SERVER=HOST\SQLEXPRESS01" in cs and "UID={u}" in cs and "PWD={pw}" in cs
    assert "Encrypt=yes" in cs and "TrustServerCertificate=yes" in cs


def test_password_with_special_chars_is_brace_escaped_and_masked(monkeypatch):
    _env(monkeypatch, DB_PASSWORD="a;b=c}d#e")
    s = load_settings()
    assert "PWD={a;b=c}}d#e}" in s.connection_string()
    safe = s.safe_connection_string()
    assert "a;b" not in safe and "PWD={***}" in safe


def test_windows_auth_omits_credentials(monkeypatch):
    _env(monkeypatch, DB_AUTH="windows", DB_USER="", DB_PASSWORD="")
    cs = load_settings().connection_string()
    assert "Trusted_Connection=yes" in cs and "UID" not in cs and "PWD" not in cs


def test_sql_auth_requires_credentials(monkeypatch):
    _env(monkeypatch, DB_PASSWORD="")
    with pytest.raises(ConfigError):
        load_settings()


def test_inline_comments_are_stripped(monkeypatch):
    _env(monkeypatch, DB_NAME="InventoryAI  # the db", GROQ_MODEL="openai/gpt-oss-20b  # note")
    s = load_settings()
    assert s.db_name == "InventoryAI" and s.groq_model == "openai/gpt-oss-20b"


@pytest.mark.parametrize("bad", ["Llama 3.1 8B", "GROQ_MODEL=openai/gpt-oss-20b"])
def test_model_must_be_an_exact_id(monkeypatch, bad):
    _env(monkeypatch, GROQ_MODEL=bad)
    with pytest.raises(ConfigError):
        load_settings()


def test_model_may_be_unset_for_non_llm_scripts(monkeypatch):
    _env(monkeypatch)
    assert load_settings().groq_model == ""
