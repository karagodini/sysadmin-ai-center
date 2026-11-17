"""SysAdmin AI Center — Streamlit-панель управления сервером через Gemini."""
import logging
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()

import streamlit as st
from google.genai import types

import agent
import auth
import models
import shell_exec

logger = logging.getLogger("ai-agent")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [ai-agent] %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False

MAX_HISTORY_MESSAGES = 30
LOG_OUTPUT_PREVIEW = 3000

st.set_page_config(
    page_title="SysAdmin AI Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def bootstrap():
    models.init_db()
    return True


try:
    bootstrap()
except Exception:
    logger.exception("bootstrap failed")
    st.error("❌ Сервис временно недоступен (нет связи с базой данных). Подробности — в журнале сервера.")
    st.stop()

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


# ================= АВТОРИЗАЦИЯ =================
if not st.session_state.get("authenticated") and not st.session_state.get("logged_out"):
    _user = auth.restore_session()
    if _user:
        st.session_state.authenticated = True
        st.session_state.username = _user

if not st.session_state.get("authenticated"):
    auth.flush_cookie()
    st.markdown("""
    <div style="display: flex; justify-content: center; align-items: center; min-height: 30vh;">
        <div style="text-align: center; margin-bottom: 8px;">
            <span style="font-size: 3.5rem;">⚡</span>
            <h2 style="margin: 12px 0 4px 0; color: #f8fafc; font-weight: 700;">SysAdmin AI Center</h2>
            <p style="color: #94a3b8; font-size: 0.9rem;">Введите учетные данные администратора</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
    _l, _c, _r = st.columns([1, 1.2, 1])
    with _c:
        with st.form("login_form"):
            login_input = st.text_input("Логин", placeholder="admin", max_chars=100)
            pass_input = st.text_input("Пароль", type="password", placeholder="••••••••", max_chars=200)
            submit_btn = st.form_submit_button("Войти в систему", use_container_width=True)
        if submit_btn:
            try:
                ok, msg = auth.try_login(login_input.strip(), pass_input)
            except Exception:
                logger.exception("login error")
                ok, msg = False, "Внутренняя ошибка входа. Попробуйте позже."
            if ok:
                st.session_state.authenticated = True
                st.session_state.username = "admin"
                st.session_state.pop("logged_out", None)
                st.rerun()
            else:
                st.error(f"❌ {msg}")
    st.stop()

auth.flush_cookie()
USERNAME = st.session_state.get("username", "admin")


def approver_label():
    th = st.session_state.get("token_hash", "")
    return f"{USERNAME}@{auth.client_ip()}#{th[:8]}"


# ================= СОСТОЯНИЕ ЧАТОВ =================
def load_history_from_db(chat_id):
    try:
        msgs = models.load_messages(chat_id)
    except Exception:
        logger.exception("load_messages failed")
        return []
    return [{"role": "model" if m["role"] in ("assistant", "model") else "user", "content": m["content"]}
            for m in msgs]


if "current_chat_id" not in st.session_state:
    _chats = models.list_chats()
    st.session_state.current_chat_id = _chats[0]["id"] if _chats else models.create_chat()
if "messages" not in st.session_state:
    st.session_state.messages = load_history_from_db(st.session_state.current_chat_id)
st.session_state.setdefault("model_cursor", 0)
st.session_state.setdefault("pending_prompt", None)
st.session_state.setdefault("turn", None)


def switch_chat(chat_id):
    st.session_state.current_chat_id = chat_id
    st.session_state.messages = load_history_from_db(chat_id)
    st.session_state.turn = None


# ================= БОКОВАЯ ПАНЕЛЬ =================
with st.sidebar:
    st.markdown(f"""
    <div style="display: flex; align-items: center; justify-content: space-between; background: rgba(30, 41, 59, 0.6); padding: 10px 14px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.08); margin-bottom: 16px;">
        <div style="display: flex; align-items: center; gap: 8px;">
            <span style="font-size: 1.2rem;">👤</span>
            <span style="font-weight: 600; color: #f8fafc; font-size: 0.95rem;">{USERNAME}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 Выйти из системы", use_container_width=True):
        auth.logout()
        st.rerun()

    st.markdown("---")
    st.markdown("### 💬 Чаты")
    turn_active = st.session_state.turn is not None

    if st.button("➕ Новый чат", use_container_width=True, disabled=turn_active):
        switch_chat(models.create_chat())
        st.rerun()

    for chat in models.list_chats():
        chat_id = chat["id"]
        title = chat["title"] or f"Чат #{chat_id}"
        is_active = chat_id == st.session_state.current_chat_id
        col_chat, col_del = st.columns([0.8, 0.2])
        with col_chat:
            if st.button(title[:25], key=f"chat_{chat_id}", type="primary" if is_active else "secondary",
                         use_container_width=True, disabled=turn_active):
                switch_chat(chat_id)
                st.rerun()
        with col_del:
            if st.button("🗑️", key=f"del_{chat_id}", use_container_width=True, disabled=turn_active):
                models.delete_chat(chat_id)
                if st.session_state.current_chat_id == chat_id:
                    remaining = models.list_chats()
                    switch_chat(remaining[0]["id"] if remaining else models.create_chat())
                st.rerun()

# ================= ОСНОВНОЙ ИНТЕРФЕЙС =================
st.markdown("""
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
    <div>
        <div class="main-title">🤖 SysAdmin AI Center</div>
        <div class="subtitle">Управление сервером через ИИ-агента · изменяющие команды — только после подтверждения</div>
    </div>
    <div>
        <div class="status-badge">
            <div class="status-dot"></div> Агент активен
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

QUICK = [
    ("📊 Статус системы", "Покажи текущий статус системы: свободная память, CPU, аптайм и нагрузку."),
    ("💾 Место на диске", "Проверь свободное место на всех дисках (df -h) и размер /var/log."),
    ("🔝 Топ CPU/RAM", "Покажи топ-5 процессов по использованию CPU и оперативной памяти."),
    ("🔍 Статус служб", "Проверь статус служб nginx и ai-agent на сервере."),
    ("📁 Логи системы", "Покажи последние 20 строк журнала службы ai-agent (journalctl -u ai-agent -n 20)."),
]
st.markdown("##### ⚡ Быстрые команды:")
q_cols = st.columns(len(QUICK))
for col, (label, prompt) in zip(q_cols, QUICK):
    with col:
        if st.button(label, use_container_width=True, disabled=st.session_state.turn is not None):
            st.session_state.pending_prompt = prompt

st.markdown("<br>", unsafe_allow_html=True)

for msg in st.session_state.messages:
    ui_role = "assistant" if msg["role"] == "model" else "user"
    with st.chat_message(ui_role, avatar="🤖" if ui_role == "assistant" else "👤"):
        st.markdown(msg["content"])


# ================= ЛОГИКА ХОДА АГЕНТА =================
def _safe_block(text):
    return text.replace("```", "ʼʼʼ")


def run_command(turn, entry, mode, approved_by):
    cmd = entry["command"]
    logger.info("exec mode=%s by=%s chat=%s cmd=%r", mode, approved_by, turn["chat_id"], cmd)
    res = shell_exec.execute(cmd, entry.get("argv"))
    turn["tool_executed"] = True
    turn["model"] = turn.get("last_model")
    entry["payload"] = {"exit_code": res["exit_code"], "output": res["output"]}
    logger.info("exec done code=%s ms=%s cmd=%r", res["exit_code"], res["duration_ms"], cmd)
    try:
        models.audit_command(turn["chat_id"], cmd, mode, approved_by, res["exit_code"], res["duration_ms"])
    except Exception:
        logger.exception("audit failed")
    icon = "⚙️" if mode == "auto" else "✅"
    preview = res["output"][:LOG_OUTPUT_PREVIEW] + ("\n…" if len(res["output"]) > LOG_OUTPUT_PREVIEW else "")
    block = f"{icon} `{_safe_block(cmd)}` · {'авто' if mode == 'auto' else 'подтверждено'} · код {res['exit_code']}\n```\n{_safe_block(preview)}\n```"
    turn["log"].append(block)
    st.markdown(block)


def reject_command(turn, entry, approved_by):
    entry["payload"] = {"error": "Администратор отклонил выполнение этой команды. Не пытайся обойти отказ."}
    logger.info("exec rejected by=%s cmd=%r", approved_by, entry["command"])
    try:
        models.audit_command(turn["chat_id"], entry["command"], "rejected", approved_by, None, None)
    except Exception:
        logger.exception("audit failed")
    block = f"🚫 Отклонено: `{_safe_block(entry['command'])}`"
    turn["log"].append(block)
    st.markdown(block)


def _decide(turn_id, idx, decision):
    turn = st.session_state.turn
    if turn and turn["id"] == turn_id and idx < len(turn["batch"] or []):
        turn["batch"][idx]["decision"] = decision
        turn["batch"][idx]["decided_by"] = approver_label()


def process_batch(turn):
    """Выполняет/отклоняет команды текущей пачки. True — пачка закрыта."""
    waiting = False
    for entry in turn["batch"]:
        if "payload" in entry:
            continue
        if entry["auto"]:
            run_command(turn, entry, "auto", "auto-allowlist")
        elif entry.get("decision") == "approve":
            run_command(turn, entry, "approved", entry.get("decided_by"))
        elif entry.get("decision") == "reject":
            reject_command(turn, entry, entry.get("decided_by"))
        else:
            waiting = True
    if waiting:
        return False
    parts = [agent.function_response_part(e["call"], e["payload"]) for e in turn["batch"]]
    turn["contents"].append(types.Content(role="user", parts=parts))
    turn["batch"] = None
    return True


def render_pending(turn):
    for idx, entry in enumerate(turn["batch"]):
        if "payload" in entry or entry["auto"]:
            continue
        with st.container(border=True):
            st.markdown("🛡️ **Агент предлагает выполнить команду** (нужно подтверждение):")
            st.code(entry["command"], language="bash")
            if entry.get("reason"):
                st.caption(f"Зачем: {entry['reason']}")
            st.caption(f"Почему не автоматически: {entry['why']}")
            c1, c2, _ = st.columns([1, 1, 3])
            c1.button("✅ Выполнить", key=f"ap_{turn['id']}_{idx}", type="primary",
                      on_click=_decide, args=(turn["id"], idx, "approve"))
            c2.button("🚫 Отклонить", key=f"rj_{turn['id']}_{idx}",
                      on_click=_decide, args=(turn["id"], idx, "reject"))


def finish_turn(turn, extra=None):
    if extra:
        turn["log"].append(extra)
        st.markdown(extra)
    content = "\n\n".join(turn["log"]).strip() or "(нет ответа)"
    if turn.get("last_model"):
        content += f"\n\n_🚀 Модель: `{turn['last_model']}`_"
    try:
        models.save_message(turn["chat_id"], "model", content)
    except Exception:
        logger.exception("save_message failed")
    if turn["chat_id"] == st.session_state.current_chat_id:
        st.session_state.messages.append({"role": "model", "content": content})
    st.session_state.turn = None


def run_turn(turn):
    """Двигает ход агента. True — ход завершён, False — ждём подтверждения."""
    if turn["batch"] is not None and not process_batch(turn):
        render_pending(turn)
        return False

    while turn["rounds"] < agent.MAX_TOOL_ROUNDS:
        turn["rounds"] += 1
        if turn["model"]:
            candidates = [turn["model"]]
        else:
            cur = st.session_state.model_cursor
            candidates = agent.FALLBACK_MODELS[cur:] + agent.FALLBACK_MODELS[:cur]

        result = None
        for model_name in candidates:
            ph = st.empty()
            try:
                with st.spinner(f"⚡ Думаю… (`{model_name}`)"):
                    parts, text = agent.stream_once(model_name, turn["contents"],
                                                    lambda t, _ph=ph: _ph.markdown(t + " ▌"))
                if text.strip():
                    ph.markdown(text)
                else:
                    ph.empty()
                result = (model_name, parts, text)
                break
            except Exception as exc:
                logger.warning("model %s failed (tool_executed=%s): %s: %s", model_name, turn["tool_executed"],
                               type(exc).__name__, str(exc)[:300])
                ph.empty()
                if turn["tool_executed"]:
                    finish_turn(turn, f"❌ Модель `{model_name}` не ответила после выполнения команд. "
                                      "Чтобы не выполнять команды повторно, запрос остановлен — "
                                      "уточните задачу новым сообщением.")
                    return True
                st.caption(f"⚠️ Модель `{model_name}` сейчас недоступна, пробую следующую…")
                st.session_state.model_cursor = (agent.FALLBACK_MODELS.index(model_name) + 1) % len(agent.FALLBACK_MODELS)

        if result is None:
            finish_turn(turn, "❌ Не удалось получить ответ ни от одной модели. Попробуйте позже.")
            return True

        model_name, parts, text = result
        turn["last_model"] = model_name
        st.session_state.model_cursor = agent.FALLBACK_MODELS.index(model_name)
        turn["contents"].append(types.Content(role="model", parts=parts))
        if text.strip():
            turn["log"].append(text.strip())

        calls = [p.function_call for p in parts if p.function_call]
        if not calls:
            finish_turn(turn)
            return True

        batch = []
        for call in calls:
            cmd, reason = agent.call_args(call)
            if call.name != "run_shell" or not cmd:
                batch.append({"call": call, "command": cmd or "", "auto": False, "why": "",
                              "payload": {"error": "Неизвестный инструмент или пустая команда."}})
                continue
            auto, argv, why = shell_exec.classify(cmd)
            batch.append({"call": call, "command": cmd, "reason": reason, "auto": auto,
                          "argv": argv, "why": why})
        turn["batch"] = batch
        if not process_batch(turn):
            render_pending(turn)
            return False

    finish_turn(turn, "⏹ Достигнут лимит шагов агента за один запрос. Продолжите новым сообщением.")
    return True


# ================= ВВОД =================
user_input = st.chat_input("Напишите задачу (например: проверь место на диске)...",
                           disabled=st.session_state.turn is not None, max_chars=8000)
prompt_to_send = None
if user_input:
    prompt_to_send = user_input
elif st.session_state.pending_prompt and st.session_state.turn is None:
    prompt_to_send = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

if prompt_to_send and st.session_state.turn is None:
    chat_id = st.session_state.current_chat_id
    if not st.session_state.messages:
        models.rename_chat(chat_id, prompt_to_send[:40])
    st.session_state.messages.append({"role": "user", "content": prompt_to_send})
    models.save_message(chat_id, "user", prompt_to_send)
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt_to_send)
    history = st.session_state.messages[-MAX_HISTORY_MESSAGES:]
    st.session_state.turn = {
        "id": uuid.uuid4().hex[:12],
        "chat_id": chat_id,
        "contents": agent.history_to_contents(history),
        "model": None,
        "last_model": None,
        "tool_executed": False,
        "batch": None,
        "log": [],
        "rounds": 0,
    }

turn = st.session_state.turn
if turn is not None:
    if turn["chat_id"] != st.session_state.current_chat_id:
        st.session_state.turn = None
    else:
        with st.chat_message("assistant", avatar="🤖"):
            for block in list(turn["log"]):
                st.markdown(block)
            done = run_turn(turn)
            if not done:
                if st.button("⏹ Отменить запрос", key=f"cancel_{turn['id']}"):
                    for e in turn["batch"] or []:
                        if "payload" not in e and not e["auto"]:
                            e["decision"] = "reject"
                            e["decided_by"] = approver_label()
                    process_batch(turn)
                    finish_turn(turn, "⏹ Запрос отменён администратором.")
                    st.rerun()
        if done:
            st.rerun()
