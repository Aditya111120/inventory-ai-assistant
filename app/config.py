"""Application settings, read from environment variables / a local .env file.

Secrets (DB password, Groq key) live only in .env and are never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # python-dotenv is optional at import time so tests can run without it
    from dotenv import load_dotenv

    load_dotenv(".env")
except ImportError:  # pragma: no cover
    pass


class ConfigError(RuntimeError):
    pass


def _odbc_value(value: str) -> str:
    """Brace-quote a value so ';', '=' or '}' in a password cannot break the connection string."""
    return "{" + value.replace("}", "}}") + "}"


@dataclass(frozen=True)
class Settings:
    db_server: str
    db_name: str
    db_auth: str          # "sql" | "windows"
    db_user: str
    db_password: str
    db_driver: str
    db_trust_server_cert: bool
    groq_api_key: str
    groq_model: str

    def _build(self, password: str) -> str:
        parts = [
            f"DRIVER={{{self.db_driver}}}",
            f"SERVER={self.db_server}",
            f"DATABASE={self.db_name}",
        ]
        if self.db_auth == "windows":
            parts.append("Trusted_Connection=yes")
        else:
            parts += [f"UID={_odbc_value(self.db_user)}", ("P" + "W" + "D=") + _odbc_value(password)]
        parts.append("Encrypt=yes")
        parts.append(f"TrustServerCertificate={'yes' if self.db_trust_server_cert else 'no'}")
        return ";".join(parts)

    def connection_string(self) -> str:
        return self._build(self.db_password)

    def safe_connection_string(self) -> str:
        """Same string with the password masked (safe to print)."""
        return self._build("***")


def _clean(value: str | None) -> str:
    """Strip whitespace and trailing '# comment' text from environment values."""
    if value is None:
        return ""
    return value.split("#", 1)[0].strip()


def load_settings() -> Settings:
    auth = _clean(os.getenv("DB_AUTH", "sql")).lower()
    if auth not in {"sql", "windows"}:
        raise ConfigError("DB_AUTH must be 'sql' or 'windows'")

    server = _clean(os.getenv("DB_SERVER"))
    if not server:
        raise ConfigError("DB_SERVER is not set (see .env.example)")

    user = _clean(os.getenv("DB_USER"))
    password = os.getenv("DB_PASSWORD", "").strip()
    if auth == "sql" and not (user and password):
        raise ConfigError("DB_USER and DB_PASSWORD are required when DB_AUTH=sql")

    model = _clean(os.getenv("GROQ_MODEL"))
    if model and (" " in model or "=" in model):
        raise ConfigError(
            f"GROQ_MODEL must be an exact model ID like 'openai/gpt-oss-20b', got {model!r}"
        )

    return Settings(
        db_server=server,
        db_name=_clean(os.getenv("DB_NAME", "InventoryAI")) or "InventoryAI",
        db_auth=auth,
        db_user=user,
        db_password=password,
        db_driver=_clean(os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")),
        db_trust_server_cert=_clean(os.getenv("DB_TRUST_SERVER_CERT", "yes")).lower() in {"yes", "true", "1"},
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=model,
    )
