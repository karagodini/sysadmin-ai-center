# SysAdmin AI Center (ai.ikaragodin.com.ru)

Streamlit-панель: чат с Gemini (SDK `google-genai`), который может выполнять команды на сервере.

## Как устроено на сервере
- Рабочий репозиторий: `/root/ai-agent` (git). Выкладка: `deploy/deploy.sh` → `/opt/ai-agent`.
- Служба: `ai-agent.service`, пользователь `aiagent` (не root), порт `127.0.0.1:8501`, nginx-vhost `ai-agent`.
- Секреты: `/etc/ai-agent/ai-agent.env` (только root): `GCP_API_KEY`, `ADMIN_USER`, `ADMIN_PASS_HASH` (scrypt), `DATABASE_URL`.
- sudo для агента: только `/etc/sudoers.d/ai-agent` (nginx -t, reload/restart nginx и нескольких служб).

## Безопасность выполнения команд
- Только-чтение команды из белого списка (`shell_exec.py`: df, free, uptime, ps, ss, ls/cat не-секретных файлов,
  journalctl, systemctl status, nginx -t) выполняются сразу.
- Всё остальное — только после кнопки «Выполнить»; каждое выполнение пишется в журнал и таблицу `command_audit`.
- Если после выполнения команды модель упала, запрос не повторяется другой моделью.

## Вход
- Пароль хранится как scrypt-хэш. Сменить пароль:
  `/opt/ai-agent/venv/bin/python -c "import auth,getpass;print(auth.hash_password(getpass.getpass()))"` (из `/opt/ai-agent`)
  и заменить `ADMIN_PASS_HASH` в `/etc/ai-agent/ai-agent.env`, затем `systemctl restart ai-agent`.
- Сессия: cookie `aiac_session` на 30 дней (в БД хранится только SHA-256 токена), выход удаляет сессию.
- 5 неудачных попыток с одного IP за 15 минут → блокировка IP на 15 минут (таблица `login_attempts`).
