import os
import sqlite3
import threading
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "")


class _SqliteBackend:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn = None
        self._path = Path(__file__).parent / "data" / "bot.db"

    def conn(self):
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    role TEXT NOT NULL DEFAULT 'buyer',
                    opt_requested INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.commit()
        return self._conn

    def execute(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self.conn().execute(sql, params)
            self.conn().commit()
            return cur


class _PostgresBackend:
    def __init__(self, url: str):
        import psycopg

        self._conn = psycopg.connect(url)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                role TEXT NOT NULL DEFAULT 'buyer',
                opt_requested INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()):
        def _map(value):
            if value is True or value == 1:
                return 1
            if value is False or value == 0:
                return 0
            return value

        sql = sql.replace("?", "%s")
        mapped = tuple(_map(p) for p in params)
        cur = self._conn.execute(sql, mapped)
        self._conn.commit()
        return cur


_backend = _PostgresBackend(DATABASE_URL) if DATABASE_URL else _SqliteBackend()


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    row = dict(row)
    row["opt_requested"] = 1 if row.get("opt_requested") else 0
    return row


def get_user(user_id: int):
    cur = _backend.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return _row_to_dict(cur.fetchone())


def upsert_user(user_id: int, username: str, full_name: str) -> dict:
    _backend.execute(
        """
        INSERT INTO users (user_id, username, full_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username,
                                           full_name = excluded.full_name
        """,
        (user_id, username, full_name),
    )
    row = get_user(user_id)
    return dict(row) if row is not None else {"user_id": user_id, "username": username, "full_name": full_name}


def set_role(user_id: int, role: str):
    _backend.execute("UPDATE users SET role = ?, opt_requested = 0 WHERE user_id = ?", (role, user_id))


def request_opt(user_id: int):
    _backend.execute("UPDATE users SET opt_requested = 1 WHERE user_id = ?", (user_id,))


def pending_requests() -> list[dict]:
    rows = _backend.execute(
        "SELECT * FROM users WHERE opt_requested = 1 AND role != 'wholesaler' ORDER BY user_id"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def wholesalers() -> list[dict]:
    rows = _backend.execute(
        "SELECT * FROM users WHERE role = 'wholesaler' ORDER BY user_id"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def clear_opt_request(user_id: int):
    _backend.execute("UPDATE users SET opt_requested = 0 WHERE user_id = ?", (user_id,))