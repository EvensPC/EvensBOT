import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_ERROR = ""


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
        self._url = url
        self._conn = None
        self._connect()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        # dict_row обязателен: код дальше работает со строками как со словарями.
        self._conn = psycopg.connect(self._url, row_factory=dict_row)
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

    def _run(self, sql: str, params: tuple):
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur

    def execute(self, sql: str, params: tuple = ()):
        def _map(value):
            if value is True or value == 1:
                return 1
            if value is False or value == 0:
                return 0
            return value

        sql = sql.replace("?", "%s")
        mapped = tuple(_map(p) for p in params)
        try:
            return self._run(sql, mapped)
        except Exception as e:
            # База могла перезапуститься (бесплатный тариф Render) — пробуем переподключиться.
            logger.warning("PostgreSQL: ошибка запроса (%s), переподключаюсь…", e)
            try:
                self._conn.close()
            except Exception:
                pass
            self._connect()
            return self._run(sql, mapped)


if DATABASE_URL:
    try:
        _backend = _PostgresBackend(DATABASE_URL)
    except Exception as e:  # бот должен запуститься даже при недоступной БД
        DB_ERROR = f"{type(e).__name__}: {e}"
        logger.error(
            "Не удалось подключиться к PostgreSQL (%s). Работаю на SQLite — "
            "роли оптовиков будут сбрасываться при рестарте.",
            DB_ERROR,
        )
        _backend = _SqliteBackend()
else:
    _backend = _SqliteBackend()


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


def users_count() -> int:
    row = _backend.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    if row is None:
        return 0
    return int(row["c"])


def clear_opt_request(user_id: int):
    _backend.execute("UPDATE users SET opt_requested = 0 WHERE user_id = ?", (user_id,))


def read_wholesaler_ids(path: str) -> set[int]:
    """Читает файл с ID оптовиков (по одному на строку, # — комментарий)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            ids = set()
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    ids.add(int(line))
                except ValueError:
                    continue
        return ids
    except FileNotFoundError:
        return set()


def wholesaler_file_report(path: str) -> dict:
    """Сводка по файлу оптовиков: сколько ID в файле и сколько из них уже есть в базе."""
    ids = read_wholesaler_ids(path)
    known = 0
    for user_id in ids:
        if get_user(user_id) is not None:
            known += 1
    return {"total": len(ids), "known": known, "missing": len(ids) - known}


def sync_wholesalers_from_file(path: str) -> list[int]:
    """Читает файл с ID оптовиков (по одному на строку) и выдаёт роль, если такой пользователь есть.

    Возвращает список ID, которым роль была выдана в этот раз.
    """
    ids = read_wholesaler_ids(path)

    granted = []
    for user_id in ids:
        user = get_user(user_id)
        if user is not None and user["role"] != "wholesaler":
            set_role(user_id, "wholesaler")
            granted.append(user_id)
    return granted