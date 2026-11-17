"""Политика и выполнение shell-команд, предложенных моделью.

Только-чтение команды из узкого белого списка выполняются сразу.
Всё остальное выполняется лишь после нажатия «Выполнить» администратором.
"""
import logging
import re
import shlex
import subprocess
import tempfile
import time

log = logging.getLogger("ai-agent")

TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 20000

SAFE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "HOME": "/var/lib/ai-agent",
    "TERM": "dumb",
    "PAGER": "cat",
    "SYSTEMD_PAGER": "",
    "SYSTEMD_COLORS": "0",
}

# Любой из этих символов => команда считается «сложной» и никогда не выполняется автоматически.
SHELL_META = re.compile(r"[|&;<>`$(){}\[\]\n\r\\*?!~#'\"]")

# Пути/аргументы, похожие на секреты, не читаются автоматически.
SECRET_RE = re.compile(
    r"(\.env|env\b|secret|passw|token|credential|\.pem|\.key\b|privkey|id_rsa|id_ed25519|id_ecdsa|"
    r"shadow|gshadow|sudoers|\.ssh|/proc/|\.pgpass|\.netrc|\.git-credentials|letsencrypt|"
    r"\.bash_history|\.psql_history|api.?key|\.npmrc|\.pypirc|\.docker/config|wallet|\.gnupg)",
    re.I,
)

READ_FILE_CMDS = {"cat", "head", "tail", "wc", "stat", "ls", "file", "du"}
NOARG_SAFE = {"df", "free", "uptime", "date", "hostname", "whoami", "id", "uname", "nproc",
              "lscpu", "lsblk", "ps", "w", "who", "ss", "vmstat", "iostat", "top", "lsof"}
SYSTEMCTL_SAFE = {"status", "is-active", "is-enabled", "is-failed", "list-units", "list-timers",
                  "list-unit-files", "--failed"}
JOURNAL_BAD = re.compile(r"^(-f|--follow|--rotate|--vacuum.*|--flush|--sync|--relinquish-var|"
                         r"--smart-relinquish-var|--setup-keys|--update-catalog|--output-fields.*|-o|--output.*)$")


def _split(command):
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _strip_sudo(argv):
    if argv and argv[0] == "sudo":
        argv = argv[1:]
        if argv and argv[0] == "-n":
            argv = argv[1:]
    return argv


def classify(command):
    """Возвращает (auto: bool, argv | None, причина)."""
    command = (command or "").strip()
    if not command:
        return False, None, "пустая команда"
    if len(command) > 500:
        return False, None, "слишком длинная команда"
    if SHELL_META.search(command):
        return False, None, "содержит спецсимволы оболочки"
    argv = _split(command)
    if not argv:
        return False, None, "не удалось разобрать"
    used_sudo = argv[0] == "sudo"
    core = _strip_sudo(argv)
    if not core:
        return False, None, "пустая команда"
    prog = core[0].rsplit("/", 1)[-1]
    args = core[1:]

    # nginx -t — единственная авто-команда через sudo (разрешена в sudoers)
    if prog == "nginx" and args == ["-t"]:
        return True, ["sudo", "-n", "/usr/sbin/nginx", "-t"], "проверка конфигурации nginx"
    if used_sudo:
        return False, None, "требует sudo"

    if any(SECRET_RE.search(a) for a in args):
        return False, None, "аргумент похож на секрет"

    if prog in NOARG_SAFE:
        if prog == "ss" and any(a in ("-K", "--kill") for a in args):
            return False, None, "ss --kill"
        if prog == "top" and "-b" not in args and not any(a.startswith("-b") for a in args):
            return True, ["top", "-b", "-n", "1"] + [a for a in args if a not in ("-b",)], "top (batch)"
        if prog == "lsof" and args:
            return False, None, "lsof с аргументами"
        return True, [prog] + args, "только чтение"

    if prog in READ_FILE_CMDS:
        if prog == "tail" and any(a in ("-f", "-F", "--follow", "--retry") or a.startswith("--follow") for a in args):
            return False, None, "tail -f"
        return True, [prog] + args, "чтение файлов"

    if prog == "journalctl":
        if any(JOURNAL_BAD.match(a) for a in args):
            return False, None, "опасный флаг journalctl"
        extra = ["--no-pager"]
        if not any(a in ("-n", "--lines") or a.startswith(("-n", "--lines=", "--since", "-S")) for a in args):
            extra += ["-n", "200"]
        return True, ["journalctl"] + args + extra, "чтение журнала"

    if prog == "systemctl":
        verbs = [a for a in args if not a.startswith("-") or a == "--failed"]
        if verbs and verbs[0] in SYSTEMCTL_SAFE:
            return True, ["systemctl", "--no-pager"] + args, "статус служб"
        return False, None, "systemctl с изменением состояния"

    if prog == "docker" and args[:1] in (["ps"], ["images"]):
        return True, ["docker"] + args, "только чтение"

    if prog == "ip" and args[:1] in (["a"], ["addr"], ["r"], ["route"], ["link"], ["-br"]) and \
            not any(a in ("add", "del", "delete", "set", "flush", "change", "replace") for a in args):
        return True, ["ip"] + args, "только чтение"

    return False, None, "нет в белом списке"


def _read_capped(f):
    f.seek(0)
    data = f.read(MAX_OUTPUT_CHARS * 4 + 1)
    text = data.decode("utf-8", errors="replace")
    truncated = len(text) > MAX_OUTPUT_CHARS
    return text[:MAX_OUTPUT_CHARS] + ("\n…[вывод обрезан]" if truncated else "")


def execute(command, argv=None):
    """Выполняет команду. argv — безопасный список (shell=False).
    Если argv нет (сложная команда, подтверждённая вручную), используется bash -c.
    Возвращает dict(output, exit_code, duration_ms)."""
    if argv is None:
        parsed = _split(command)
        if parsed and not SHELL_META.search(command):
            argv = parsed
        else:
            argv = ["/bin/bash", "-c", command]
    started = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.run(
                argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                env=SAFE_ENV, cwd="/tmp", timeout=TIMEOUT_SECONDS, shell=False,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
            err.write(f"\n[таймаут {TIMEOUT_SECONDS} с]".encode())
        except FileNotFoundError:
            code = 127
            err.write("команда не найдена".encode())
        except Exception:
            log.exception("execute failed")
            code = 1
            err.write("внутренняя ошибка выполнения".encode())
        stdout = _read_capped(out)
        stderr = _read_capped(err)
    output = stdout
    if stderr.strip():
        output += ("\n" if output else "") + "[stderr]\n" + stderr
    if not output.strip():
        output = "(пустой вывод)"
    return {"output": output[:MAX_OUTPUT_CHARS], "exit_code": code,
            "duration_ms": int((time.monotonic() - started) * 1000)}
