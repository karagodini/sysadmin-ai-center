#!/bin/bash
# Выкладка из рабочего репозитория /root/ai-agent в /opt/ai-agent (запуск от root).
set -euo pipefail
SRC=/root/ai-agent; DST=/opt/ai-agent
for f in app.py agent.py auth.py models.py shell_exec.py requirements.txt RULES.md .streamlit/config.toml; do
  install -D -m 640 -o root -g aiagent "$SRC/$f" "$DST/$f"
done
"$DST/venv/bin/pip" install -q -r "$DST/requirements.txt"
chown -R root:aiagent "$DST"; chmod -R u=rwX,g=rX,o= "$DST"
systemctl restart ai-agent
sleep 5; systemctl is-active ai-agent
