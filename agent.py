"""Диалог с Gemini (google-genai) с ручным вызовом инструментов и подтверждением команд."""
import logging
import os

from google import genai
from google.genai import types

import shell_exec

log = logging.getLogger("ai-agent")

FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-4-argon",
]
MAX_TOOL_ROUNDS = 8
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

_client = None


def client():
    global _client
    if _client is None:
        key = os.environ.get("GCP_API_KEY")
        if not key:
            raise RuntimeError("GCP_API_KEY не задан")
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    return _client


def config():
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[types.Tool(function_declarations=[RUN_SHELL])],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def history_to_contents(messages):
    out = []
    for m in messages:
        role = "model" if m["role"] in ("model", "assistant") else "user"
        if m.get("content"):
            out.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
    return out


def stream_once(model, contents, on_text):
    """Один запрос со стримингом. Возвращает (parts, text). Исключения пробрасываются."""
    parts, text = [], ""
    for chunk in client().models.generate_content_stream(model=model, contents=contents, config=config()):
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
    return parts, text


def function_response_part(call, payload):
    return types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=payload))


def call_args(call):
    args = call.args or {}
    return str(args.get("command", "")).strip(), str(args.get("reason", "")).strip()
