"""Диалог с LLM (Gemini через google-genai и OpenAI-совместимые провайдеры) с ручным вызовом
inструментов и подтверждением команд."""
import json
import logging
import os
import re
import time
import uuid

import openai
from google import genai
from google.genai import types

import shell_exec

log = logging.getLogger("ai-agent")

FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-4-hydrogen",
]
MAX_TOOL_ROUNDS = 8
MAX_AUTO_CANDIDATES = 4  # сколько моделей пробовать за один шаг в режиме «авто»
REQUEST_TIMEOUT_MS = 60_000

SUDO_ALLOWED = (
    "sudo -n nginx -t; sudo -n systemctl reload nginx; sudo -n systemctl restart nginx|portfolio-api|"
    "bravput-app|vera-api|dengi-bot|govorun"
)

SYSTEM_INSTRUCTION = f"""
Ты — системный администратор и ИИ-агент на Linux-сервере пользователя (Ubuntu).
Для работы с сервером у тебя есть инструмент `run_shell`.
Правила:
- Простые команды только для чтения (df, free, uptime, ps, ss, ls, cat, journalctl, systemctl status, nginx -t)
  выполняются сразу. Любые изменяющие команды выполняются ТОЛЬКО после подтверждения администратором,
  поэтому всегда кратко объясняй в поле `reason`, зачем нужна команда.
- Ты работаешь от непривилегированного пользователя `aiagent`. Через sudo разрешено только: {SUDO_ALLOWED}.
  Остальное с sudo не сработает — в таком случае предложи администратору команду для ручного выполнения.
- Не читай и не выводи секреты (.env, ключи, пароли, токены).
- Предпочитай одну простую команду без конвейеров `|`, `&&`, `;` — так она сможет выполниться автоматически.
- Если команду отклонили — не пытайся обойти отказ.
- Не спрашивай подтверждения текстом и не жди «да» в чате: если команда нужна, сразу вызывай `run_shell` —
  интерфейс сам покажет администратору кнопки «Выполнить / Отклонить».
Общайся на русском языке, кратко и по делу.
"""

RUN_SHELL = types.FunctionDeclaration(
    name="run_shell",
    description="Выполнить shell-команду на сервере. Изменяющие команды ждут подтверждения администратора.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Команда bash"},
            "reason": {"type": "string", "description": "Зачем нужна команда (коротко, по-русски)"},
        },
        "required": ["command"],
    },
)

OA_TOOL = {
    "type": "function",
    "function": {
        "name": RUN_SHELL.name,
        "description": RUN_SHELL.description,
        "parameters": RUN_SHELL.parameters_json_schema,
    },
}

# ---------------- Провайдеры ----------------
# models: (id, подпись). Порядок = порядок перебора в режиме «авто» внутри провайдера.
# disabled: причина, по которой провайдер выключен (проверено 17.11.2026). Включить без правки кода:
# AI_AGENT_ENABLE_PROVIDERS=sambanova,opencode в /etc/ai-agent/ai-agent.env.
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "kind": "gemini",
        "key_env": "GCP_API_KEY",
        "models": [(m, m) for m in FALLBACK_MODELS],
    },
    "unorouter": {
        "label": "UnoRouter",
        "kind": "openai",
        "base_url": "https://api.unorouter.com/v1",
        "key_env": "UNOROUTER_API_KEY",
        "models": [
            # проверены 17.11.2026 (ответ + вызов инструмента); лимит UnoRouter на free: 1 или 5 запросов/мин на модель
            ("gpt-oss-120b:free", "GPT-OSS 120B (free, 5/мин)"),
            ("mistral-small-3.2:free", "Mistral Small 3.2 (free, 5/мин, медленная)"),
            ("gemini-3-flash:free", "Gemini 3 Flash (free)"),
            ("agnes-3.0-flash:free", "Agnes 3.0 Flash (free)"),
            ("codestral-latest:free", "Codestral (free)"),
            ("agnes-2.5-flash:free", "Agnes 2.5 Flash (free)"),
            ("gpt-6-luna:free", "GPT-6 Luna (free)"),
            ("minimax-m3.1-flash:free", "MiniMax M3.1 Flash (free)"),
            ("gemini-3.6-flash:free", "Gemini 3.6 Flash (free)"),
            ("gemma-4-31b-it:free", "Gemma 4 31B (free, 5/мин, медленная)"),
            # открыты согласием на unorouter.com/consents; вызов инструментов ещё не проверен
            ("deepseek-v4.1-flash:free", "DeepSeek V4.1 Flash (free, не проверена)"),
            ("qwen3.7-flash:free", "Qwen 3.7 Flash (free, не проверена)"),
            ("glm-5.3-flash:free", "GLM-5.3 Flash (free, не проверена)"),
            ("llama-3.3-70b:free", "Llama 3.3 70B (free, 5/мин, не проверена)"),
            ("nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B (free, 5/мин, не проверена)"),
        ],
    },
    "sambanova": {
        "label": "SambaNova",
        "kind": "openai",
        "base_url": "https://api.sambanova.ai/v1",
        "key_env": "SAMBANOVA_API_KEY",
        "models": [
            ("gpt-oss-120b", "GPT-OSS 120B"),
            ("GLM-5.3", "GLM-5.3"),
            ("MiniMax-M3.1-Flash", "MiniMax M3.1 Flash"),
            ("Meta-Llama-3.3-70B-Instruct", "Llama 3.3 70B"),
            ("gemma-4-31B-it", "Gemma 4 31B"),
        ],
        "disabled": "ключ рабочий, но в аккаунте нет способа оплаты (402 PAYMENT_METHOD_REQUIRED)",
    },
    "opencode": {
        "label": "OpenCode Zen",
        "kind": "openai",
        "base_url": "https://opencode.ai/zen/v1",
        "key_env": "OPENCODE_API_KEY",
        "models": [
            ("glm-5.3", "GLM-5.3"),
            ("kimi-k3", "Kimi K3"),
            ("deepseek-v4-pro", "DeepSeek V4 Pro"),
            ("deepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
            ("minimax-m3", "MiniMax M3"),
            ("glm-5.3-flash-free", "GLM-5.3 Flash (free)"),
            ("deepseek-v4.1-flash-free", "DeepSeek V4.1 Flash (free)"),
        ],
        "disabled": "ключ рабочий, но баланс 0 (402), а бесплатные модели по API исчерпаны (429)",
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "kind": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "key_env": "NVIDIA_API_KEY",
        "models": [
            ("nvidia/nemotron-3-ultra-550b-a55b", "Nemotron 3 Ultra 550B"),
            ("mistralai/mistral-large-4-1t-a52b", "Mistral Large 4"),
            ("z-ai/glm-5.3-flash", "GLM-5.3 Flash"),
            ("minimaxai/minimax-m3.1-flash", "MiniMax M3.1 Flash"),
            ("nvidia/nemotron-3.5-lightning-30b-a3b", "Nemotron 3.5 Lightning 30B"),
            ("openai/gpt-oss-20b", "GPT-OSS 20B"),
        ],
        "disabled": "ключ отклонён (401): NVIDIA ждёт ключ вида nvapi-… с build.nvidia.com",
    },
}
DEFAULT_CHOICE = "gemini/auto"


def _force_enabled():
    return {p.strip() for p in os.environ.get("AI_AGENT_ENABLE_PROVIDERS", "").split(",") if p.strip()}


def provider_status(pid):
    """None — провайдер доступен, иначе строка с причиной."""
    p = PROVIDERS[pid]
    if not os.environ.get(p["key_env"]):
        return f"не задан {p['key_env']}"
    if p.get("disabled") and pid not in _force_enabled():
        return p["disabled"]
    return None


def choices():
    """Список (choice_id, подпись) для выпадающего списка."""
    out = []
    for pid, p in PROVIDERS.items():
        if provider_status(pid):
            continue
        out.append((f"{pid}/auto", f"{p['label']} · авто (запасные внутри провайдера)"))
        out.extend((f"{pid}/{mid}", f"{p['label']} · {label}") for mid, label in p["models"])
    return out


def parse_choice(choice):
    """'provider/model' -> (provider, model|None). model=None — режим авто."""
    pid, _, model = (choice or DEFAULT_CHOICE).partition("/")
    if pid not in PROVIDERS or provider_status(pid):
        pid, model = "gemini", "auto"
    known = [m for m, _ in PROVIDERS[pid]["models"]]
    if model != "auto" and model not in known:
        model = "auto"
    return pid, (None if model == "auto" else model)


def provider_models(pid):
    return [m for m, _ in PROVIDERS[pid]["models"]]


def model_label(pid, model):
    return f"{PROVIDERS.get(pid, {}).get('label', pid)} · {model}"


_SECRET_ENVS = [p["key_env"] for p in PROVIDERS.values()]


def short_error(exc):
    """Короткое описание ошибки провайдера без секретов."""
    msg = f"{type(exc).__name__}: {exc}"
    for env in _SECRET_ENVS:
        v = os.environ.get(env)
        if v:
            msg = msg.replace(v, "***")
    msg = re.sub(r"(sk-|oc_sk_|nvapi-)[A-Za-z0-9_\-]{8,}", r"\1***", msg)
    return msg[:300]


class ToolCall:
    """Единый вид вызова инструмента для всех провайдеров."""
    def __init__(self, id, name, args, raw=None):
        self.id, self.name, self.args, self.raw = id, name, args or {}, raw


# ---------------- Gemini ----------------
_gclient = None


def _gemini():
    global _gclient
    if _gclient is None:
        key = os.environ.get("GCP_API_KEY")
        if not key:
            raise RuntimeError("GCP_API_KEY не задан")
        _gclient = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    return _gclient


def _gemini_config():
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[types.Tool(function_declarations=[RUN_SHELL])],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def _gemini_stream(model, contents, on_text):
    parts, text = [], ""
    for chunk in _gemini().models.generate_content_stream(model=model, contents=contents, config=_gemini_config()):
        if not chunk.candidates:
            continue
        cand = chunk.candidates[0]
        if not cand.content or not cand.content.parts:
            continue
        for p in cand.content.parts:
            parts.append(p)
            if p.text and not p.thought:
                text += p.text
                on_text(text)
    if not parts:
        raise RuntimeError("пустой ответ модели")
    calls = [ToolCall(p.function_call.id, p.function_call.name, dict(p.function_call.args or {}), p.function_call)
             for p in parts if p.function_call]
    return parts, text, calls


# ---------------- OpenAI-совместимые ----------------
_oclients = {}
_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.S)


def _openai(pid):
    if pid not in _oclients:
        p = PROVIDERS[pid]
        _oclients[pid] = openai.OpenAI(base_url=p["base_url"], api_key=os.environ[p["key_env"]],
                                       timeout=REQUEST_TIMEOUT_MS / 1000, max_retries=0)
    return _oclients[pid]


def clean_text(text):
    return _THINK_RE.sub("", text or "").strip()


MAX_RATE_WAIT_S = 65


def _retry_after(exc):
    """Сколько секунд просит подождать провайдер при 429 (None — неизвестно)."""
    try:
        h = exc.response.headers.get("retry-after")
        if h:
            return float(h)
    except Exception:
        pass
    m = re.search(r"retry in (\d+)\s*s", str(exc))
    return float(m.group(1)) if m else None


def _openai_create(pid, model, messages, on_text, wait_rate_limit=True):
    """Запрос со стримингом. При 429 с коротким ожиданием (free-тарифы: 1 запрос/мин) ждём и повторяем
    ту же модель один раз — запрос был отклонён целиком, поэтому команды при этом не повторяются."""
    kw = dict(model=model, messages=messages, tools=[OA_TOOL], tool_choice="auto", stream=True)
    try:
        return _openai(pid).chat.completions.create(**kw)
    except openai.RateLimitError as exc:
        wait = _retry_after(exc)
        if not wait_rate_limit or wait is None or wait > MAX_RATE_WAIT_S:
            raise
        log.info("rate limit on %s/%s, waiting %.0fs", pid, model, wait)
        on_text(f"⏳ Лимит бесплатного тарифа `{model}` — жду {int(wait) + 1} с и повторяю запрос…")
        time.sleep(wait + 1)
        return _openai(pid).chat.completions.create(**kw)


def _openai_stream(pid, model, messages, on_text, wait_rate_limit=True):
    stream = _openai_create(pid, model, messages, on_text, wait_rate_limit)
    text, tcs = "", {}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        if delta.content:
            text += delta.content
            shown = clean_text(text)
            if shown:
                on_text(shown)
        for tc in delta.tool_calls or []:
            idx = tc.index if tc.index is not None else len(tcs)
            e = tcs.setdefault(idx, {"id": None, "name": "", "args": "", "extra": {}})
            if tc.id:
                e["id"] = tc.id
            if tc.function:
                if tc.function.name and not e["name"]:
                    e["name"] = tc.function.name
                if tc.function.arguments:
                    e["args"] += tc.function.arguments
            extra = getattr(tc, "model_extra", None) or {}
            e["extra"].update({k: v for k, v in extra.items() if v is not None})
    text = clean_text(text)
    calls, raw_calls = [], []
    for idx in sorted(tcs):
        e = tcs[idx]
        cid = e["id"] or f"call_{uuid.uuid4().hex[:12]}"
        try:
            args = json.loads(e["args"]) if e["args"].strip() else {}
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}
        raw = {"id": cid, "type": "function", "function": {"name": e["name"], "arguments": e["args"] or "{}"}}
        raw.update(e["extra"])
        raw_calls.append(raw)
        calls.append(ToolCall(cid, e["name"], args, raw))
    if not text and not calls:
        raise RuntimeError("пустой ответ модели")
    msg = {"role": "assistant", "content": text or None}
    if raw_calls:
        msg["tool_calls"] = raw_calls
    return msg, text, calls


# ---------------- Общий интерфейс ----------------
def history_to_contents(pid, messages):
    kind = PROVIDERS[pid]["kind"]
    out = [] if kind == "gemini" else [{"role": "system", "content": SYSTEM_INSTRUCTION.strip()}]
    for m in messages:
        if not m.get("content"):
            continue
        is_model = m["role"] in ("model", "assistant")
        if kind == "gemini":
            out.append(types.Content(role="model" if is_model else "user", parts=[types.Part(text=m["content"])]))
        else:
            out.append({"role": "assistant" if is_model else "user", "content": m["content"]})
    return out


def stream_once(pid, model, contents, on_text, wait_rate_limit=True):
    """Один запрос со стримингом. Возвращает (assistant_raw, text, [ToolCall]). Исключения пробрасываются."""
    if PROVIDERS[pid]["kind"] == "gemini":
        return _gemini_stream(model, contents, on_text)
    return _openai_stream(pid, model, contents, on_text, wait_rate_limit)


def append_assistant(pid, contents, raw):
    if PROVIDERS[pid]["kind"] == "gemini":
        contents.append(types.Content(role="model", parts=raw))
    else:
        contents.append(raw)


def append_tool_results(pid, contents, results):
    """results: [(ToolCall, payload_dict)]"""
    if PROVIDERS[pid]["kind"] == "gemini":
        parts = [types.Part(function_response=types.FunctionResponse(id=c.id, name=c.name, response=p))
                 for c, p in results]
        contents.append(types.Content(role="user", parts=parts))
    else:
        for c, p in results:
            contents.append({"role": "tool", "tool_call_id": c.id, "name": c.name,
                             "content": json.dumps(p, ensure_ascii=False)})


def call_args(call):
    args = call.args or {}
    return str(args.get("command", "")).strip(), str(args.get("reason", "")).strip()
