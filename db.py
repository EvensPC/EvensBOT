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
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cart_items (
                    user_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    qty INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (user_id, code)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    name TEXT,
                    phone TEXT,
                    address TEXT,
                    comment TEXT,
                    total INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_items (
                    order_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    qty INTEGER NOT NULL DEFAULT 1
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

    def lastrowid(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self.conn().execute(sql, params)
            self.conn().commit()
            return cur.lastrowid


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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                user_id BIGINT NOT NULL,
                code TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, code)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TEXT NOT NULL,
                name TEXT,
                phone TEXT,
                address TEXT,
                comment TEXT,
                total INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                order_id BIGINT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                qty INTEGER NOT NULL DEFAULT 1
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

    def lastrowid(self, sql: str, params: tuple = ()):
        sql = sql.replace("?", "%s")
        mapped = tuple(_map(p) for p in params)
        cur = self._conn.execute(sql + " RETURNING id", mapped)
        self._conn.commit()
        return cur.fetchone()[0]


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


# ---------------- Корзина ----------------

def add_to_cart(user_id: int, code: str, qty: int = 1):
    _backend.execute(
        """
        INSERT INTO cart_items (user_id, code, qty) VALUES (?, ?, ?)
        ON CONFLICT(user_id, code) DO UPDATE SET qty = qty + excluded.qty
        """,
        (user_id, code, qty),
    )


def set_cart_qty(user_id: int, code: str, qty: int):
    if qty <= 0:
        _backend.execute("DELETE FROM cart_items WHERE user_id = ? AND code = ?", (user_id, code))
    else:
        _backend.execute(
            "UPDATE cart_items SET qty = ? WHERE user_id = ? AND code = ?",
            (qty, user_id, code),
        )


def remove_from_cart(user_id: int, code: str):
    _backend.execute("DELETE FROM cart_items WHERE user_id = ? AND code = ?", (user_id, code))


def clear_cart(user_id: int):
    _backend.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))


def get_cart(user_id: int) -> list[tuple[str, int]]:
    rows = _backend.execute(
        "SELECT code, qty FROM cart_items WHERE user_id = ? ORDER BY code", (user_id,)
    ).fetchall()
    return [(r["code"], r["qty"]) for r in rows]


def get_cart_qty(user_id: int, code: str) -> int:
    row = _backend.execute(
        "SELECT qty FROM cart_items WHERE user_id = ? AND code = ?", (user_id, code)
    ).fetchone()
    return int(row[0]) if row else 0


def cart_count(user_id: int) -> int:
    row = _backend.execute(
        "SELECT COALESCE(SUM(qty), 0) FROM cart_items WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row[0]) if row else 0


# ---------------- Заказы ----------------

def create_order(user_id: int, name: str, phone: str, address: str, comment: str, total: int) -> int:
    from datetime import datetime, timezone

    cur = _backend.lastrowid(
        """
        INSERT INTO orders (user_id, created_at, name, phone, address, comment, total)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, datetime.now(timezone.utc).isoformat(), name, phone, address, comment, total),
    )
    return cur


def add_order_item(order_id: int, code: str, name: str, price: int, qty: int):
    _backend.execute(
        "INSERT INTO order_items (order_id, code, name, price, qty) VALUES (?, ?, ?, ?, ?)",
        (order_id, code, name, price, qty),
    )


def get_order(order_id: int) -> dict | None:
    row = _backend.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def get_order_items(order_id: int) -> list[dict]:
    rows = _backend.execute(
        "SELECT code, name, price, qty FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    return [dict(r) for r in rows]