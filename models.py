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
        options="-c statement_timeout=15000",  # любой запрос к БД максимум 15 сек
    )


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def save_message(role, content):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO chat_messages (role, content) VALUES (%s, %s)", (role, content))
    conn.commit()
    cur.close()
    conn.close()


def load_messages():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT role, content FROM chat_messages ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def clear_messages():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages")
    conn.commit()
    cur.close()
    conn.close()