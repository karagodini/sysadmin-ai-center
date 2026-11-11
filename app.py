import os
import subprocess
import time
import shutil
from dotenv import load_dotenv

load_dotenv()

import google.generativeai as genai
import streamlit as st
from models import (
    init_db, create_chat, list_chats, rename_chat,
    delete_chat, save_message, load_messages,
)

# ================= НАСТРОЙКИ =================
API_KEY = os.getenv("GCP_API_KEY")
APP_PASSWORD = os.getenv("ADMIN_PASS")

if not API_KEY:
    raise ValueError("Ошибка: GCP_API_KEY не найден в файле .env")
if not APP_PASSWORD:
    raise ValueError("Ошибка: ADMIN_PASS не найден в файле .env")

FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-4-argon",
]

MODEL_SWITCH_DELAY_SECONDS = 1.5
BASH_TIMEOUT_SECONDS = 120
MAX_HISTORY_MESSAGES = 30
# =============================================

init_db()

st.set_page_config(
    page_title="SysAdmin AI Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    .stApp {
        background: radial-gradient(circle at 50% -20%, #1e1b4b 0%, #0f172a 60%, #090d16 100%);
        color: #f8fafc;
    }

    header[data-testid="stHeader"] { background: transparent !important; }

    section[data-testid="stSidebar"] {
        background-color: rgba(15, 23, 42, 0.75) !important;
        backdrop-filter: blur(12px);
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }

    .sys-card {
        background: rgba(30, 41, 59, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 14px;
        margin-bottom: 12px;
        backdrop-filter: blur(8px);
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }

    .metric-label {
        font-size: 0.75rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-weight: 600;
    }

    .metric-value { font-size: 1.4rem; font-weight: 700; color: #38bdf8; }

    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #a855f7 0%, #6366f1 50%, #3b82f6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 4px;
        display: flex;
        align-items: center;
        gap: 12px;
    }

    .subtitle { color: #94a3b8; font-size: 0.95rem; margin-bottom: 24px; }

    .status-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: rgba(16, 185, 129, 0.15);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.3);
        padding: 4px 10px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 600;
    }

    .status-dot {
        width: 8px;
        height: 8px;
        background-color: #10b981;
        border-radius: 50%;
        box-shadow: 0 0 8px #10b981;
    }

    [data-testid="stChatMessage"] {
        background-color: rgba(30, 41, 59, 0.5) !important;
        border: 1px solid rgba(255, 255, 255, 0.06) !important;
        border-radius: 16px !important;
        padding: 16px !important;
        margin-bottom: 14px !important;
        backdrop-filter: blur(10px);
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background-color: rgba(99, 102, 241, 0.12) !important;
        border: 1px solid rgba(99, 102, 241, 0.25) !important;
    }

    div[data-baseweb="input"] {
        border-radius: 14px !important;
        background-color: rgba(30, 41, 59, 0.8) !important;
        border: 1px solid rgba(255, 255, 255, 0.15) !important;
    }

    div[data-baseweb="input"]:focus-within {
        border-color: #6366f1 !important;
        box-shadow: 0 0 15px rgba(99, 102, 241, 0.3) !important;
    }

    .stButton > button {
        border-radius: 10px !important;
        background: rgba(30, 41, 59, 0.7) !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        color: #e2e8f0 !important;
        font-size: 0.85rem !important;
        transition: all 0.2s ease-in-out !important;
    }

    .stButton > button:hover {
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important;
        border-color: #818cf8 !important;
        color: #ffffff !important;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
    }
</style>
""", unsafe_allow_html=True)


def get_system_stats():
    try:
        disk = shutil.disk_usage("/")
        disk_pct = int(disk.used / disk.total * 100)
        with open('/proc/meminfo') as f:
            lines = f.readlines()
        mem_total = int(lines[0].split()[1])
        mem_avail = int(lines[2].split()[1])
        ram_pct = int((mem_total - mem_avail) / mem_total * 100)
        return ram_pct, disk_pct
    except Exception:
        return 0, 0


def execute_bash(command: str) -> str:
    """Выполняет bash-команду на сервере и возвращает результат (stdout и stderr)."""
    st.info(f"⚡ Выполняю в терминале: `{command}`")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[ОШИБКИ/STDERR]:\n{result.stderr}"
        output = output[:20000]
        return output if output.strip() else "Команда выполнена успешно (пустой вывод)."
    except Exception as e:
        return f"Критическая ошибка выполнения: {str(e)}"


genai.configure(api_key=API_KEY)

system_instruction = """
Ты — умный системный администратор и ИИ-агент, запущенный прямо на Linux-сервере пользователя.
У тебя есть инструмент `execute_bash`, который позволяет тебе выполнять команды в терминале этого сервера.

Если пользователь просит что-то сделать с файлами, проверить систему, установить пакеты или подключиться по SSH к другому серверу — НЕ пиши просто инструкции, а САМ вызывай функцию `execute_bash`, анализируй вывод и сообщай результат пользователю.

НИКОГДА не изменяй файлы /root/ai-agent/app.py и /root/ai-agent/models.py и не запускай скрипты, которые их модифицируют. Это код твоего собственного интерфейса — ты себя сломаешь.

Общайся на русском языке, кратко и по делу.
"""


def to_gemini_history(messages: list) -> list:
    return [{"role": m["role"], "parts": [m["content"]]} for m in messages]


def create_gemini_chat(model_name: str, history: list):
    model = genai.GenerativeModel(
        model_name=model_name,
        tools=[execute_bash],
        system_instruction=system_instruction,
    )
    return model.start_chat(
        history=history,
        enable_automatic_function_calling=True,
    )


def load_history_from_db(chat_id):
    try:
        msgs = load_messages(chat_id)
    except Exception:
        return []
    normalized = []
    for m in msgs:
        role = "model" if m["role"] in ("assistant", "model") else "user"
        normalized.append({"role": role, "content": m["content"]})
    return normalized[-MAX_HISTORY_MESSAGES:]


# ================= СОСТОЯНИЕ =================
if "current_chat_id" not in st.session_state:
    chats = list_chats()
    st.session_state.current_chat_id = chats[0]["id"] if chats else create_chat()

if "messages" not in st.session_state:
    st.session_state.messages = load_history_from_db(st.session_state.current_chat_id)

if "model_cursor" not in st.session_state:
    st.session_state.model_cursor = 0

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None


# ================= БОКОВАЯ ПАНЕЛЬ =================
with st.sidebar:
    st.markdown("### 🔐 Авторизация")
    password = st.text_input("Пароль доступа:", type="password", key="app_pwd")

    if password != APP_PASSWORD:
        st.warning("🔒 Введите пароль для доступа к серверу.")
        st.stop()

    st.markdown("---")
    st.markdown("### 🖥️ Мониторинг сервера")

    ram_used, disk_used = get_system_stats()
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"""
        <div class="sys-card">
            <div class="metric-label">RAM Нагрузка</div>
            <div class="metric-value">{ram_used}%</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="sys-card">
            <div class="metric-label">Диск Занят</div>
            <div class="metric-value">{disk_used}%</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("""
    <div class="status-badge">
        <div class="status-dot"></div> Memory Persisted (PostgreSQL)
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 💬 Чаты")

    if st.button("➕ Новый чат", use_container_width=True):
        st.session_state.current_chat_id = create_chat()
        st.session_state.messages = []
        st.session_state.model_cursor = 0
        st.rerun()

    for chat in list_chats():
        col_a, col_b = st.columns([0.8, 0.2])
        is_current = chat["id"] == st.session_state.current_chat_id
        title = chat["title"]
        display_title = title[:18] + "…" if len(title) > 18 else title
        with col_a:
            if st.button(
                display_title,
                key=f"chat_{chat['id']}",
                type="primary" if is_current else "secondary",
                use_container_width=True,
            ):
                st.session_state.current_chat_id = chat["id"]
                st.session_state.messages = load_history_from_db(chat["id"])
                st.session_state.model_cursor = 0
                st.rerun()
        with col_b:
            if st.button("🗑️", key=f"del_{chat['id']}", help="Удалить чат"):
                delete_chat(chat["id"])
                if st.session_state.current_chat_id == chat["id"]:
                    remaining = list_chats()
                    if remaining:
                        st.session_state.current_chat_id = remaining[0]["id"]
                        st.session_state.messages = load_history_from_db(remaining[0]["id"])
                    else:
                        st.session_state.current_chat_id = create_chat()
                        st.session_state.messages = []
                st.rerun()


# ================= ОСНОВНОЙ ИНТЕРФЕЙС =================
st.markdown("""
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
    <div>
        <div class="main-title">🤖 SysAdmin AI Center</div>
        <div class="subtitle">Управление сервером через ИИ-агента · история в PostgreSQL</div>
    </div>
    <div>
        <div class="status-badge">
            <div class="status-dot"></div> Агент активен
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("##### ⚡ Быстрые команды:")
q_cols = st.columns(5)

with q_cols[0]:
    if st.button("📊 Статус системы", use_container_width=True):
        st.session_state.pending_prompt = "Покажи текущий статус системы: свободна память, CPU, аптайм и нагрузку."
with q_cols[1]:
    if st.button("💾 Место на диске", use_container_width=True):
        st.session_state.pending_prompt = "Проверь свободное место на всех дисках (df -h) и размер /var/log."
with q_cols[2]:
    if st.button("🔝 Топ CPU/RAM", use_container_width=True):
        st.session_state.pending_prompt = "Покажи топ-5 процессов по использованию CPU и оперативной памяти."
with q_cols[3]:
    if st.button("🔍 Статус служб", use_container_width=True):
        st.session_state.pending_prompt = "Проверь статус служб nginx и streamlit-panel на сервере."
with q_cols[4]:
    if st.button("📁 Логи системы", use_container_width=True):
        st.session_state.pending_prompt = "Покажи последние 20 строк из лога journalctl -u streamlit-panel."

st.markdown("<br>", unsafe_allow_html=True)

for msg in st.session_state.messages:
    ui_role = "assistant" if msg["role"] == "model" else "user"
    avatar = "🤖" if ui_role == "assistant" else "👤"
    with st.chat_message(ui_role, avatar=avatar):
        st.markdown(msg["content"])

user_input = st.chat_input("Напишите задачу (например: установи htop)...")

prompt_to_send = None
if user_input:
    prompt_to_send = user_input
elif st.session_state.pending_prompt:
    prompt_to_send = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

if prompt_to_send:
    if not st.session_state.messages:
        rename_chat(st.session_state.current_chat_id, prompt_to_send[:40])

    st.session_state.messages.append({"role": "user", "content": prompt_to_send})
    print("[AGENT] saving user message to DB...", flush=True)
    save_message(st.session_state.current_chat_id, "user", prompt_to_send)
    print("[AGENT] user message saved", flush=True)

    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt_to_send)

    history_messages = st.session_state.messages[:-1][-MAX_HISTORY_MESSAGES:]
    previous_history = to_gemini_history(history_messages)

    original_cursor = st.session_state.model_cursor
    ordered_models = (
        FALLBACK_MODELS[original_cursor:] + FALLBACK_MODELS[:original_cursor]
    )

    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("⚡ Выполняю запрос и обрабатываю команды..."):
            last_error = None
            answered = False

            for offset, model_name in enumerate(ordered_models):
                try:
                    st.write(f"🔄 Обращаюсь к модели `{model_name}`...")
                    print(f"[AGENT] request -> {model_name}", flush=True)
                    chat_session = create_gemini_chat(model_name, previous_history)
                    response = chat_session.send_message(
                        prompt_to_send,
                        request_options={"timeout": 90},
                    )
                    print(f"[AGENT] response <- {model_name}", flush=True)
                    answer = (response.text or "").strip()

                    if not answer:
                        raise RuntimeError("Empty response: model returned no text")

                    st.markdown(answer)
                    st.caption(f"🚀 Активная модель: `{model_name}`")

                    st.session_state.messages.append({"role": "model", "content": answer})
                    save_message(st.session_state.current_chat_id, "model", answer)

                    st.session_state.model_cursor = (original_cursor + offset) % len(FALLBACK_MODELS)
                    answered = True
                    break

                except Exception as e:
                    print(f"[AGENT] error {model_name}: {e}", flush=True)
                    last_error = e
                    st.warning(f"⚠️ Модель `{model_name}` не ответила: `{e}`. Переключаюсь на резервную...")
                    st.session_state.model_cursor = (original_cursor + offset + 1) % len(FALLBACK_MODELS)
                    time.sleep(MODEL_SWITCH_DELAY_SECONDS)
                    continue

            if not answered:
                st.error(f"❌ Ошибка получения ответа от ИИ. Подробности: `{last_error}`")