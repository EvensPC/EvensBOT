import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bot.db"

_lock = threading.Lock()
_conn = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
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
        _conn.commit()
    return _conn


def _execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = get_conn().execute(sql, params)
        get_conn().commit()
        return cur


def get_user(user_id: int):
    row = _execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row is not None else None


def upsert_user(user_id: int, username: str, full_name: str) -> dict:
    _execute(
        """
        INSERT INTO users (user_id, username, full_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username,
                                           full_name = excluded.full_name
        """,
        (user_id, username, full_name),
    )
    row = get_user(user_id)
    return dict(row)


def set_role(user_id: int, role: str):
    _execute("UPDATE users SET role = ?, opt_requested = 0 WHERE user_id = ?", (role, user_id))


def request_opt(user_id: int):
    _execute("UPDATE users SET opt_requested = 1 WHERE user_id = ?", (user_id,))


def pending_requests() -> list[dict]:
    rows = _execute(
        "SELECT * FROM users WHERE opt_requested = 1 AND role != 'wholesaler' ORDER BY user_id"
    ).fetchall()
    return [dict(r) for r in rows]


def clear_opt_request(user_id: int):
    _execute("UPDATE users SET opt_requested = 0 WHERE user_id = ?", (user_id,))