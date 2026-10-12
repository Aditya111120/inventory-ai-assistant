"""Initialize one demo account password without echoing or logging it."""
from __future__ import annotations

import argparse
import getpass
import os

from argon2 import PasswordHasher
from app.config import load_settings
from app.db import connect


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    args = parser.parse_args()
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if not first or first != second:
        raise SystemExit("Passwords do not match or are empty.")
    settings = load_settings()
    conn = connect(settings)
    try:
        ph = PasswordHasher()
        cur = conn.cursor()
        cur.execute("SELECT user_id,is_active,password_hash FROM dbo.AppUsers WHERE username=?", [args.username])
        row = cur.fetchone()
        if not row or not row.is_active:
            raise SystemExit("Unknown or inactive user.")
        if row.password_hash:
            raise SystemExit("Password is already initialized; refusing to overwrite it.")
        cur.execute("UPDATE dbo.AppUsers SET password_hash=? WHERE user_id=? AND password_hash IS NULL", [ph.hash(first), row.user_id])
        conn.commit()
        print(f"Password initialized for {args.username}.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
