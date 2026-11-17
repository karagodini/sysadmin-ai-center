"""Работа с PostgreSQL: чаты, сообщения, сессии входа, попытки входа, аудит команд."""
import os
import threading
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

load_dotenv()

_pool = None
_pool_lock = threading.Lock()
_init_done = False


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                dsn = os.environ.get("DATABASE_URL")
                if not dsn:
                    raise RuntimeError("DATABASE_URL не задан в окружении")
                _pool = ThreadedConnectionPool(
                    1, 8, dsn,
                    cursor_factory=RealDictCursor,
                    connect_timeout=5,
                    options="-c statement_timeout=15000",
                )
    return _pool


@contextmanager
def db():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db():
    """Создаёт/мигрирует схему. Вызывается один раз на процесс."""
    global _init_done
    if _init_done:
        return
    with db() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'Новый чат',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        cur.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                chat_id INTEGER REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        cur.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS chat_id INTEGER REFERENCES chats(id) ON DELETE CASCADE")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat ON chat_messages(chat_id)")
        # 13.11 таблицу chats пересоздали со схемой чужого приложения (user_email NOT NULL) —
        # из-за этого «Новый чат» падал. Даём колонке значение по умолчанию.
        cur.execute("""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_schema = 'public' AND table_name = 'chats' AND column_name = 'user_email') THEN
                ALTER TABLE chats ALTER COLUMN user_email SET DEFAULT 'admin';
              END IF;
            END $$;""")
        cur.execute("ALTER TABLE chats ALTER COLUMN title SET DEFAULT 'Новый чат'")
        # Возвращаем в список чаты, сообщения которых остались без записи в chats.
        cur.execute("""
            INSERT INTO chats (id, title, created_at, updated_at)
            SELECT m.chat_id,
                   COALESCE(LEFT((SELECT x.content FROM chat_messages x
                                  WHERE x.chat_id = m.chat_id AND x.role = 'user' ORDER BY x.id LIMIT 1), 40),
                            'Восстановленный чат'),
                   MIN(m.timestamp), MAX(m.timestamp)
            FROM chat_messages m
            WHERE m.chat_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM chats c WHERE c.id = m.chat_id)
            GROUP BY m.chat_id""")
        cur.execute("SELECT setval(pg_get_serial_sequence('chats', 'id'), GREATEST((SELECT COALESCE(MAX(id), 1) FROM chats), 1))")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id SERIAL PRIMARY KEY,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )""")
        cur.execute("ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS username TEXT")
        cur.execute("ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS ip TEXT")
        cur.execute("DELETE FROM auth_sessions WHERE expires_at < CURRENT_TIMESTAMP")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id SERIAL PRIMARY KEY,
                ip TEXT,
                username TEXT,
                success BOOLEAN NOT NULL,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ts ON login_attempts(ts)")
        cur.execute("DELETE FROM login_attempts WHERE ts < CURRENT_TIMESTAMP - INTERVAL '30 days'")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS command_audit (
                id SERIAL PRIMARY KEY,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                chat_id INTEGER,
                command TEXT NOT NULL,
                mode TEXT NOT NULL,
                approved_by TEXT,
                exit_code INTEGER,
                duration_ms INTEGER
            )""")
        # выбор модели на чат и модель, ответившая на сообщение
        cur.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS model TEXT")
        cur.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS model TEXT")
    _init_done = True


# ---------- чаты ----------
def create_chat(title="Новый чат"):
    with db() as cur:
        cur.execute("INSERT INTO chats (title) VALUES (%s) RETURNING id", (title,))
        return cur.fetchone()["id"]


def list_chats():
    with db() as cur:
        cur.execute("SELECT id, title FROM chats ORDER BY updated_at DESC NULLS LAST, id DESC")
        return [dict(r) for r in cur.fetchall()]


def rename_chat(chat_id, title):
    with db() as cur:
        cur.execute("UPDATE chats SET title = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (title, chat_id))


def delete_chat(chat_id):
    with db() as cur:
        cur.execute("DELETE FROM chats WHERE id = %s", (chat_id,))


def get_chat_model(chat_id):
    with db() as cur:
        cur.execute("SELECT model FROM chats WHERE id = %s", (chat_id,))
        row = cur.fetchone()
        return row["model"] if row else None


def set_chat_model(chat_id, model):
    with db() as cur:
        cur.execute("UPDATE chats SET model = %s WHERE id = %s", (model, chat_id))


def save_message(chat_id, role, content, model=None):
    with db() as cur:
        cur.execute("INSERT INTO chat_messages (chat_id, role, content, model) VALUES (%s, %s, %s, %s)",
                    (chat_id, role, content, model))
        cur.execute("UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = %s", (chat_id,))


def load_messages(chat_id):
    with db() as cur:
        cur.execute("SELECT role, content, model FROM chat_messages WHERE chat_id = %s ORDER BY id ASC", (chat_id,))
        return [{"role": r["role"], "content": r["content"], "model": r["model"]} for r in cur.fetchall()]


# ---------- сессии входа ----------
def create_auth_session(token_hash, username, ip, days=30):
    with db() as cur:
        cur.execute(
            "INSERT INTO auth_sessions (token_hash, username, ip, expires_at) "
            "VALUES (%s, %s, %s, CURRENT_TIMESTAMP + (%s * INTERVAL '1 day')) RETURNING expires_at",
            (token_hash, username, ip, days),
        )
        return cur.fetchone()["expires_at"]


def touch_auth_session(token_hash, days=30):
    """Продлевает живую сессию; возвращает username или None."""
    with db() as cur:
        cur.execute(
            "UPDATE auth_sessions SET last_seen = CURRENT_TIMESTAMP, "
            "expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 day') "
            "WHERE token_hash = %s AND expires_at > CURRENT_TIMESTAMP RETURNING username",
            (days, token_hash),
        )
        row = cur.fetchone()
        return (row["username"] or "admin") if row else None


def delete_auth_session(token_hash):
    with db() as cur:
        cur.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))


# ---------- попытки входа ----------
def record_login_attempt(ip, username, success):
    with db() as cur:
        cur.execute("INSERT INTO login_attempts (ip, username, success) VALUES (%s, %s, %s)", (ip, username, success))


def failed_attempts(ip, minutes):
    """(неудачи с этого IP, неудачи всего) за последние N минут."""
    with db() as cur:
        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE ip = %s) AS by_ip, COUNT(*) AS total FROM login_attempts "
            "WHERE success = FALSE AND ts > CURRENT_TIMESTAMP - (%s * INTERVAL '1 minute')",
            (ip, minutes),
        )
        r = cur.fetchone()
        return r["by_ip"], r["total"]


# ---------- аудит команд ----------
def audit_command(chat_id, command, mode, approved_by, exit_code, duration_ms):
    with db() as cur:
        cur.execute(
            "INSERT INTO command_audit (chat_id, command, mode, approved_by, exit_code, duration_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chat_id, command, mode, approved_by, exit_code, duration_ms),
        )
