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
        background-color: rgba(15, 23, 42, 0.85) !important;
        backdrop-filter: blur(12px);
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }

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
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

# ================= СТРАНИЦА ВХОДА =================
if not st.session_state.authenticated:
    st.markdown("""
    <div style="display: flex; justify-content: center; align-items: center; min-height: 80vh;">
        <div style="background: rgba(30, 41, 59, 0.75); padding: 40px; border-radius: 20px; box-shadow: 0 15px 35px rgba(0,0,0,0.4); width: 100%; max-width: 420px; border: 1px solid rgba(255,255,255,0.12); backdrop-filter: blur(16px);">
            <div style="text-align: center; margin-bottom: 28px;">
                <span style="font-size: 3.5rem;">⚡</span>
                <h2 style="margin: 12px 0 4px 0; color: #f8fafc; font-weight: 700;">SysAdmin AI Center</h2>
                <p style="color: #94a3b8; font-size: 0.9rem;">Введите учетные данные администратора</p>
            </div>
    """, unsafe_allow_html=True)

    with st.form("login_form"):
        login_input = st.text_input("Логин", placeholder="admin")
        pass_input = st.text_input("Пароль", type="password", placeholder="••••••••")
        submit_btn = st.form_submit_button("Войти в систему", use_container_width=True)

        if submit_btn:
            if login_input == "admin" and pass_input == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ Неверный логин или пароль!")

    st.markdown("</div></div>", unsafe_allow_html=True)
    st.stop()


# Инициализация чатов после входа
if "current_chat_id" not in st.session_state:
    chats = list_chats()
    st.session_state.current_chat_id = chats[0]["id"] if chats else create_chat()

if "messages" not in st.session_state:
    st.session_state.messages = load_history_from_db(st.session_state.current_chat_id)

if "model_cursor" not in st.session_state:
    st.session_state.model_cursor = 0

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None


# ================= БОКОВАЯ ПАНЕЛЬ (ПОСЛЕ ВХОДА) =================
with st.sidebar:
    st.markdown("""
    <div style="display: flex; align-items: center; justify-content: space-between; background: rgba(30, 41, 59, 0.6); padding: 10px 14px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.08); margin-bottom: 16px;">
        <div style="display: flex; align-items: center; gap: 8px;">
            <span style="font-size: 1.2rem;">👤</span>
            <span style="font-weight: 600; color: #f8fafc; font-size: 0.95rem;">admin</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 Выйти из системы", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

    st.markdown("---")
    st.markdown("### 💬 Чаты")

    if st.button("➕ Новый чат", use_container_width=True):
        st.session_state.current_chat_id = create_chat()
        st.session_state.messages = []
        st.session_state.model_cursor = 0
        st.rerun()

    for chat in list_chats():
        chat_id = chat["id"]
        title = chat["title"] or f"Чат #{chat_id}"
        is_active = chat_id == st.session_state.current_chat_id
        col_chat, col_del = st.columns([0.8, 0.2])
        with col_chat:
            if st.button(
                title[:25],
                key=f"chat_{chat_id}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state.current_chat_id = chat_id
                st.session_state.messages = load_history_from_db(chat_id)
                st.session_state.model_cursor = 0
                st.rerun()
        with col_del:
            if st.button("🗑️", key=f"del_{chat_id}", use_container_width=True):
                delete_chat(chat_id)
                if st.session_state.current_chat_id == chat_id:
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
    save_message(st.session_state.current_chat_id, "user", prompt_to_send)

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
                    chat_session = create_gemini_chat(model_name, previous_history)
                    response = chat_session.send_message(
                        prompt_to_send,
                        request_options={"timeout": 90},
                    )
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
                    last_error = e
                    st.warning(f"⚠️ Модель `{model_name}` не ответила: `{e}`. Переключаюсь на резервную...")
                    st.session_state.model_cursor = (original_cursor + offset + 1) % len(FALLBACK_MODELS)
                    time.sleep(MODEL_SWITCH_DELAY_SECONDS)
                    continue

            if not answered:
                st.error(f"❌ Ошибка получения ответа от ИИ. Подробности: `{last_error}`")
