import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://ai_user:secure_password_123@localhost:5432/ai_agent")


def get_connection():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=5,
        options="-c statement_timeout=15000",
    )


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'Новый чат',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            chat_id INTEGER REFERENCES chats(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Миграция: если таблица осталась от одночатовой версии — добавляем chat_id
    cur.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS chat_id INTEGER REFERENCES chats(id) ON DELETE CASCADE")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat ON chat_messages(chat_id)")
    cur.execute("SELECT COUNT(*) AS c FROM chat_messages WHERE chat_id IS NULL")
    if cur.fetchone()["c"] > 0:
        cur.execute("INSERT INTO chats (title) VALUES ('Старый диалог') RETURNING id")
        old_id = cur.fetchone()["id"]
        cur.execute("UPDATE chat_messages SET chat_id = %s WHERE chat_id IS NULL", (old_id,))
    conn.commit()
    cur.close()
    conn.close()


def create_chat(title="Новый чат"):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO chats (title) VALUES (%s) RETURNING id", (title,))
    chat_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return chat_id


def list_chats():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM chats ORDER BY updated_at DESC NULLS LAST, id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def rename_chat(chat_id, title):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE chats SET title = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (title, chat_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def delete_chat(chat_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chats WHERE id = %s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()


def save_message(chat_id, role, content):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_messages (chat_id, role, content) VALUES (%s, %s, %s)",
        (chat_id, role, content),
    )
    cur.execute("UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = %s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()


def load_messages(chat_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content FROM chat_messages WHERE chat_id = %s ORDER BY id ASC",
        (chat_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def clear_messages(chat_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE chat_id = %s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()


# ===== Алиасы: совместимость со старыми именами функций =====
create_new_chat = create_chat
get_all_chats = list_chats
update_chat_title = rename_chat