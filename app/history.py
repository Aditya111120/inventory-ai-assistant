"""Small SQLite-backed user conversation history; no credentials are stored."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd


def normalize_json_value(value):
    """Normalize supported database-derived values for API and history JSON."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return normalize_json_value(value.item())
    if isinstance(value, float):
        import math

        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON metadata object keys must be strings")
        return {
            key: normalize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]
    raise TypeError(
        f"Unsupported JSON metadata value: {type(value).__name__}"
    )


class ConversationStore:
    def __init__(self, path: str | Path = "inventory_ai_history.sqlite3") -> None:
        self.path = str(path)
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, name TEXT NOT NULL, persona TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (conversation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (message_id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, message_id);
            """)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def ensure_conversation(self, user_id: str, name: str, persona: str, conversation_id: str, title: str = "New conversation") -> None:
        now = self._now()
        with self._connect() as db:
            db.execute("INSERT INTO users(user_id,name,persona) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, persona=excluded.persona", (user_id, name, persona))
            db.execute("INSERT OR IGNORE INTO conversations(conversation_id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)", (conversation_id, user_id, title, now, now))

    def add_message(self, conversation_id: str, role: str, content: str, metadata: dict | None = None) -> None:
        now = self._now()
        serialized_metadata = json.dumps(
            normalize_json_value(metadata or {}),
            allow_nan=False,
        )
        with self._connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content,metadata,created_at) VALUES(?,?,?,?,?)", (conversation_id, role, content, serialized_metadata, now))
            db.execute("UPDATE conversations SET updated_at=? WHERE conversation_id=?", (now, conversation_id))

    def set_title_if_new(self, conversation_id: str, question: str) -> None:
        title = " ".join(question.split()).strip()
        generic_questions = {
            "hi", "hello", "hey", "help", "what can you do",
            "show inventory", "inventory", "new chat",
        }
        if not title or title.casefold().rstrip("?.!") in generic_questions:
            return
        title = title[:120]
        with self._connect() as db:
            db.execute("UPDATE conversations SET title=? WHERE conversation_id=? AND title='New conversation'", (title, conversation_id))

    def recents(self, user_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
                       (SELECT m.content FROM messages m
                        WHERE m.conversation_id=c.conversation_id AND m.role='user'
                        ORDER BY m.message_id LIMIT 1) AS first_question
                FROM conversations c
                WHERE c.user_id=?
                ORDER BY c.updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [
            dict(zip(
                ("conversation_id", "title", "created_at", "updated_at", "first_question"),
                row,
            ))
            for row in rows
        ]

    def messages(self, user_id: str, conversation_id: str) -> list[dict]:
        with self._connect() as db:
            owner = db.execute("SELECT 1 FROM conversations WHERE conversation_id=? AND user_id=?", (conversation_id, user_id)).fetchone()
            if not owner:
                return []
            rows = db.execute("SELECT role,content,metadata,created_at FROM messages WHERE conversation_id=? ORDER BY message_id", (conversation_id,)).fetchall()
        return [{"role": role, "content": content, "metadata": json.loads(metadata), "created_at": created} for role, content, metadata, created in rows]

    def latest_pending(self, user_id: str, conversation_id: str) -> dict | None:
        """Return the latest server-owned pending clarification, if any."""
        with self._connect() as db:
            owner = db.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=? AND user_id=?",
                (conversation_id, user_id),
            ).fetchone()
            if not owner:
                return None
            row = db.execute(
                "SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY message_id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row[0]).get("pending_state")
        except (TypeError, ValueError, AttributeError):
            return None
        return value if isinstance(value, dict) else None

    def rename(self, user_id: str, conversation_id: str, title: str) -> bool:
        title = " ".join(title.strip().split())[:120]
        if not title:
            return False
        with self._connect() as db:
            cur = db.execute("UPDATE conversations SET title=?, updated_at=? WHERE conversation_id=? AND user_id=?", (title, self._now(), conversation_id, user_id))
        return cur.rowcount == 1

    def delete(self, user_id: str, conversation_id: str) -> bool:
        with self._connect() as db:
            owner = db.execute("SELECT 1 FROM conversations WHERE conversation_id=? AND user_id=?", (conversation_id, user_id)).fetchone()
            if not owner:
                return False
            db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM conversations WHERE conversation_id=? AND user_id=?", (conversation_id, user_id))
        return True
