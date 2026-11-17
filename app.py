"""SysAdmin AI Center — Streamlit-панель управления сервером через LLM (Gemini и OpenAI-совместимые провайдеры)."""
import logging
import sys
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

import streamlit as st
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
    return [{"role": "model" if m["role"] in ("assistant", "model") else "user", "content": m["content"],
             "model": m.get("model")} for m in msgs]


if "current_chat_id" not in st.session_state:
    _chats = models.list_chats()
    st.session_state.current_chat_id = _chats[0]["id"] if _chats else models.create_chat()
if "messages" not in st.session_state:
    st.session_state.messages = load_history_from_db(st.session_state.current_chat_id)
st.session_state.setdefault("model_cursor", {})
st.session_state.setdefault("regen", False)
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
    st.markdown("### 🧠 Модель для ответа")
    _cid = st.session_state.current_chat_id
    _opts = agent.choices()
    _ids = [c for c, _ in _opts]
    _labels = dict(_opts)
    try:
        _saved = models.get_chat_model(_cid)
    except Exception:
        logger.exception("get_chat_model failed")
        _saved = None
    _pid, _m = agent.parse_choice(_saved)
    _cur = f"{_pid}/{_m or 'auto'}"

    def _save_model_choice(chat_id, key):
        try:
            models.set_chat_model(chat_id, st.session_state[key])
        except Exception:
            logger.exception("set_chat_model failed")

    _key = f"model_sel_{_cid}"
    st.selectbox("Кто отвечает в этом чате", _ids, index=_ids.index(_cur) if _cur in _ids else 0,
                 format_func=lambda c: _labels.get(c, c), key=_key, label_visibility="collapsed",
                 disabled=st.session_state.turn is not None, on_change=_save_model_choice, args=(_cid, _key))
    st.caption("«авто» — при сбое пробует другую модель того же провайдера (пока не выполнена ни одна команда). "
               "Конкретная модель — без подмены: при ошибке покажу её.")
    _off = [(p["label"], agent.provider_status(pid)) for pid, p in agent.PROVIDERS.items() if agent.provider_status(pid)]
    if _off:
        with st.expander("Недоступные провайдеры"):
            for _lbl, _why in _off:
                st.caption(f"**{_lbl}**: {_why}")

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

for _i, msg in enumerate(st.session_state.messages):
    ui_role = "assistant" if msg["role"] == "model" else "user"
    with st.chat_message(ui_role, avatar="🤖" if ui_role == "assistant" else "👤"):
        st.markdown(msg["content"])
        if ui_role == "assistant" and msg.get("model"):
            st.caption(f"🚀 Ответила модель: {msg['model']}")
        if (ui_role == "assistant" and _i == len(st.session_state.messages) - 1
                and st.session_state.turn is None and _i > 0):
            if st.button("🔁 Ответить заново выбранной моделью", key=f"regen_{st.session_state.current_chat_id}_{_i}"):
                st.session_state.regen = True


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
    agent.append_tool_results(turn["provider"], turn["contents"], [(e["call"], e["payload"]) for e in turn["batch"]])
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
    label = agent.model_label(turn["provider"], turn["last_model"]) if turn.get("last_model") else None
    try:
        models.save_message(turn["chat_id"], "model", content, label)
    except Exception:
        logger.exception("save_message failed")
    if turn["chat_id"] == st.session_state.current_chat_id:
        st.session_state.messages.append({"role": "model", "content": content, "model": label})
    st.session_state.turn = None


def _auto_candidates(pid):
    pool = agent.provider_models(pid)
    cur = st.session_state.model_cursor.get(pid, 0) % len(pool)
    return [(pid, m) for m in (pool[cur:] + pool[:cur])[:agent.MAX_AUTO_CANDIDATES]]


def clean_history(msgs):
    """Убирает из контекста сообщения об ошибках и вопросы, на которые так и не было ответа
    (например, отклонённые фильтром), — иначе один заблокированный вопрос ломает весь чат."""
    out, n = [], len(msgs)
    for i, m in enumerate(msgs):
        if m["role"] != "user":
            if not (m.get("content") or "").lstrip().startswith("❌"):
                out.append(m)
            continue
        j, answered = i + 1, False
        while j < n and msgs[j]["role"] != "user":
            if not (msgs[j].get("content") or "").lstrip().startswith("❌"):
                answered = True
            j += 1
        if answered or i == max(k for k, x in enumerate(msgs) if x["role"] == "user"):
            out.append(m)
    return out


def run_turn(turn):
    """Двигает ход агента. True — ход завершён, False — ждём подтверждения."""
    if turn["batch"] is not None and not process_batch(turn):
        render_pending(turn)
        return False

    while turn["rounds"] < agent.MAX_TOOL_ROUNDS:
        turn["rounds"] += 1
        pid = turn["provider"]
        if turn["model"]:
            candidates = [(pid, turn["model"])]
        elif turn["fixed"]:
            candidates = [(pid, turn["fixed"])]
        else:
            candidates = _auto_candidates(pid)
            # Другие провайдеры — только на первом шаге, пока модель ещё ничего не вызывала
            if turn["rounds"] == 1 and turn.get("history") is not None:
                for other in agent.PROVIDERS:
                    if other != pid and not agent.provider_status(other):
                        candidates += _auto_candidates(other)[:2]
        single = len(candidates) == 1

        result, failures, waited, attempt = None, [], False, candidates
        while result is None:
            retry, blocked = [], set()
            for cpid, model_name in attempt:
                if cpid in blocked:
                    continue
                contents = turn["contents"] if cpid == pid else agent.history_to_contents(cpid, turn["history"])
                ph = st.empty()
                try:
                    with st.spinner(f"⚡ Думаю… (`{agent.model_label(cpid, model_name)}`)"):
                        # в «авто» при лимите сразу берём следующую модель; ждём Retry-After только для одной модели
                        raw, text, calls = agent.stream_once(cpid, model_name, contents,
                                                             lambda t, _ph=ph: _ph.markdown(t + " ▌"),
                                                             wait_rate_limit=single)
                    if text.strip():
                        ph.markdown(text)
                    else:
                        ph.empty()
                    result = (cpid, model_name, raw, text, calls, contents)
                    break
                except Exception as exc:
                    kind, ra, why = agent.classify_error(exc)
                    logger.warning("model %s/%s failed (tool_executed=%s, kind=%s): %s", cpid, model_name,
                                   turn["tool_executed"], kind, agent.short_error(exc))
                    ph.empty()
                    label = agent.model_label(cpid, model_name)
                    if turn["tool_executed"]:
                        finish_turn(turn, f"❌ Модель `{label}` не ответила после выполнения команд: {why}. "
                                          "Чтобы не выполнять команды повторно, запрос остановлен — "
                                          "уточните задачу новым сообщением.")
                        return True
                    if single:
                        hint = (" Этот запрос Gemini не пропускает — выберите модель UnoRouter."
                                if kind == "blocked" else "")
                        finish_turn(turn, f"❌ Модель `{label}` не ответила: {why}.{hint} Другую модель без "
                                          "вашего выбора не подставляю — выберите другую в боковой панели "
                                          "или нажмите «Ответить заново».")
                        return True
                    failures.append((label, why))
                    if kind == "blocked":
                        blocked.add(cpid)
                        st.caption(f"⚠️ {why} — пробую другого провайдера…")
                    else:
                        st.caption(f"⚠️ `{label}`: {why}, пробую следующую…")
                    if kind in ("rate", "busy"):
                        retry.append(((cpid, model_name), ra if ra else (20 if kind == "busy" else 60)))
                    pool = agent.provider_models(cpid)
                    if model_name in pool:
                        st.session_state.model_cursor[cpid] = (pool.index(model_name) + 1) % len(pool)
            if result is not None or waited or not retry:
                break
            waited = True
            wait_s = int(max(5, min(60, min(r for _, r in retry))))
            status = st.empty()
            for left in range(wait_s, 0, -1):
                status.info(f"⏳ Все модели сейчас упёрлись в лимит или заняты — жду {left} с и повторю…")
                time.sleep(1)
            status.empty()
            attempt = [c for c, _ in retry]

        if result is None:
            reasons = "\n".join(f"- `{lbl}`: {why}" for lbl, why in failures[-8:])
            finish_turn(turn, "❌ Не удалось получить ответ ни от одной модели. Попробуйте позже или выберите "
                              f"другую модель в боковой панели.\n\n{reasons}")
            return True

        cpid, model_name, raw, text, calls, contents = result
        if cpid != pid:
            turn["provider"], turn["contents"], pid = cpid, contents, cpid
        turn["last_model"] = model_name
        pool = agent.provider_models(pid)
        if not turn["fixed"] and model_name in pool:
            st.session_state.model_cursor[pid] = pool.index(model_name)
        agent.append_assistant(pid, turn["contents"], raw)
        if text.strip():
            turn["log"].append(text.strip())

        if not calls:
            finish_turn(turn)
            return True

        batch = []
        for call in calls:
            cmd, reason = agent.call_args(call)
            if call.name != agent.RUN_SHELL.name or not cmd:
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
_sel_pid, _sel_model = agent.parse_choice(st.session_state.get(f"model_sel_{st.session_state.current_chat_id}"))
st.caption(f"🧠 Ответит: **{agent.PROVIDERS[_sel_pid]['label']} · {_sel_model or 'авто'}** — сменить можно в боковой панели")
user_input = st.chat_input("Напишите задачу (например: проверь место на диске)...",
                           disabled=st.session_state.turn is not None, max_chars=8000)
prompt_to_send = None
if user_input and st.session_state.turn is not None:
    st.toast("⏳ Дождитесь окончания текущего ответа — сообщение не отправлено.")
elif user_input:
    prompt_to_send = user_input
elif st.session_state.pending_prompt and st.session_state.turn is None:
    prompt_to_send = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

history = None
if prompt_to_send and st.session_state.turn is None:
    st.session_state.regen = False
    chat_id = st.session_state.current_chat_id
    if not st.session_state.messages:
        models.rename_chat(chat_id, prompt_to_send[:40])
    st.session_state.messages.append({"role": "user", "content": prompt_to_send})
    models.save_message(chat_id, "user", prompt_to_send)
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt_to_send)
    history = st.session_state.messages[-MAX_HISTORY_MESSAGES:]
elif st.session_state.regen and st.session_state.turn is None:
    # «Ответить заново»: тот же последний вопрос, прежний ответ остаётся в истории чата, но модели не передаётся
    st.session_state.regen = False
    _msgs = st.session_state.messages
    _last_user = max((i for i, m in enumerate(_msgs) if m["role"] == "user"), default=None)
    if _last_user is not None:
        history = _msgs[:_last_user + 1][-MAX_HISTORY_MESSAGES:]

if history is not None:
    history = clean_history(history)
    chat_id = st.session_state.current_chat_id
    _choice = st.session_state.get(f"model_sel_{chat_id}") or models.get_chat_model(chat_id)
    _pid, _fixed = agent.parse_choice(_choice)
    st.session_state.turn = {
        "id": uuid.uuid4().hex[:12],
        "chat_id": chat_id,
        "provider": _pid,
        "fixed": _fixed,
        "history": history,
        "contents": agent.history_to_contents(_pid, history),
        "model": None,
        "last_model": None,
        "tool_executed": False,
        "batch": None,
        "log": [],
        "rounds": 0,
    }
    # Сразу перерисовываем страницу: поле ввода и кнопки станут неактивными на всё время ответа,
    # иначе новое сообщение, отправленное во время стриминга, прерывало бы ход и терялось.
    st.rerun()

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
