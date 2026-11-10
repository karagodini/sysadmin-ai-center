import subprocess
import time
import shutil
import google.generativeai as genai
import streamlit as st

# ================= НАСТРОЙКИ =================
    api_key = "REPLACE_ME"
APP_PASSWORD = "babka123"

# Список моделей для автоматического перебора
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-4-argon",
]

MODEL_SWITCH_DELAY_SECONDS = 1.5
BASH_TIMEOUT_SECONDS = 120
MAX_HISTORY_MESSAGES = 20
# =============================================

# Настройка страницы
st.set_page_config(
    page_title="SysAdmin AI Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ================= СТИЛИЗАЦИЯ И ДИЗАЙН (CSS) =================
st.markdown("""
<style>
    /* Импорт современного шрифта */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* Фон приложения */
    .stApp {
        background: radial-gradient(circle at 50% -20%, #1e1b4b 0%, #0f172a 60%, #090d16 100%);
        color: #f8fafc;
    }

    /* Скрытие дефолтных шапок Streamlit */
    header[data-testid="stHeader"] {
        background: transparent !important;
    }

    /* Боковая панель */
    section[data-testid="stSidebar"] {
        background-color: rgba(15, 23, 42, 0.75) !important;
        backdrop-filter: blur(12px);
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }

    /* Карточки метрик и статусов */
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

    .metric-value {
        font-size: 1.4rem;
        font-weight: 700;
        color: #38bdf8;
    }

    /* Заголовок основной */
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

    .subtitle {
        color: #94a3b8;
        font-size: 0.95rem;
        margin-bottom: 24px;
    }

    /* Бейдж онлайн статуса */
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

    /* Сообщения в чате */
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

    /* Поле ввода */
    div[data-baseweb="input"] {
        border-radius: 14px !important;
        background-color: rgba(30, 41, 59, 0.8) !important;
        border: 1px solid rgba(255, 255, 255, 0.15) !important;
    }

    div[data-baseweb="input"]:focus-within {
        border-color: #6366f1 !important;
        box-shadow: 0 0 15px rgba(99, 102, 241, 0.3) !important;
    }

    /* Кнопки быстрых команд */
    .quick-btn-container {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-bottom: 20px;
    }

    /* Стилизация кнопок Streamlit */
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


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_system_stats():
    """Сбор быстрой статистики сервера без сторонних библиотек"""
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
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[ОШИБКИ/STDERR]:\n{result.stderr}"
        return output if output.strip() else "Команда выполнена успешно (пустой вывод)."
    except Exception as e:
        return f"Критическая ошибка выполнения: {str(e)}"


# Инициализация Gemini API
genai.configure(api_key=API_KEY)

system_instruction = """
Ты — умный системный администратор и ИИ-агент, запущенный прямо на Linux-сервере пользователя.
У тебя есть инструмент `execute_bash`, который позволяет тебе выполнять команды в терминале этого сервера.

Если пользователь просит что-то сделать с файлами, проверить систему, установить пакеты или подключиться по SSH к другому серверу — НЕ пиши просто инструкции, а САМ вызывай функцию `execute_bash`, анализируй вывод и сообщай результат пользователю.

Общайся на русском языке, кратко и по делу.
"""


def to_gemini_history(messages: list) -> list:
    return [
        {
            "role": message["role"],
            "parts": [message["content"]],
        }
        for message in messages
    ]


def create_chat(model_name: str, history: list):
    model = genai.GenerativeModel(
        model_name=model_name,
        tools=[execute_bash],
        system_instruction=system_instruction,
    )
    return model.start_chat(
        history=history,
        enable_automatic_function_calling=True,
    )


# ================= БОКОВАЯ ПАНЕЛЬ (SIDEBAR) =================
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
    st.markdown("### ⚙️ Статус службы")
    st.markdown("""
    <div class="status-badge">
        <div class="status-dot"></div> Auto-Start Active (Systemd)
    </div>
    <div style="font-size:0.75rem; color:#94a3b8; margin-top:8px;">
        🔄 Изменения в коде применяются автоматически без перезапуска.
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    if st.button("🗑️ Очистить диалог", use_container_width=True):
        st.session_state.messages = []
        st.session_state.model_cursor = 0
        st.rerun()


# ================= ОСНОВНОЙ ИНТЕРФЕЙС =================
st.markdown("""
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
    <div>
        <div class="main-title">🤖 SysAdmin AI Center</div>
        <div class="subtitle">Управление сервером через ИИ-агента с автоматическим исполнением команд</div>
    </div>
    <div>
        <div class="status-badge">
            <div class="status-dot"></div> Авто-обновление включено
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# Состояние диалога
if "messages" not in st.session_state:
    st.session_state.messages = []

if "model_cursor" not in st.session_state:
    st.session_state.model_cursor = 0

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None


# Быстрые действия (Quick Presets)
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
        st.session_state.pending_prompt = "Проверь статус служб nginx и ai-agent на сервере."

with q_cols[4]:
    if st.button("📁 Логи системы", use_container_width=True):
        st.session_state.pending_prompt = "Покажи последние 20 строк из лога journalctl -u ai-agent."

st.markdown("<br>", unsafe_allow_html=True)


# Отрисовка истории сообщений
for msg in st.session_state.messages:
    ui_role = "assistant" if msg["role"] == "model" else msg["role"]
    avatar = "🤖" if ui_role == "assistant" else "👤"
    with st.chat_message(ui_role, avatar=avatar):
        st.markdown(msg["content"])


# Ввод пользователя (текстом или по кнопке быстрого действия)
user_input = st.chat_input("Напишите задачу (например: установи htop)...")

prompt_to_send = None
if user_input:
    prompt_to_send = user_input
elif st.session_state.pending_prompt:
    prompt_to_send = st.session_state.pending_prompt
    st.session_state.pending_prompt = None


if prompt_to_send:
    st.session_state.messages.append({"role": "user", "content": prompt_to_send})
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
                    chat = create_chat(model_name, previous_history)
                    response = chat.send_message(prompt_to_send)
                    answer = (response.text or "").strip()

                    if not answer:
                        raise RuntimeError("Empty response: model returned no text")

                    st.markdown(answer)
                    st.caption(f"🚀 Активная модель: `{model_name}`")

                    st.session_state.messages.append({"role": "model", "content": answer})
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
