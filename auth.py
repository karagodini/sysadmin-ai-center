"""Пароль (scrypt-хэш), постоянные сессии через cookie и ограничение попыток входа."""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

import streamlit as st

import models

log = logging.getLogger("ai-agent")

COOKIE_NAME = "aiac_session"
SESSION_DAYS = 30
LOCK_WINDOW_MIN = 15
MAX_FAILS_PER_IP = 5
MAX_FAILS_TOTAL = 30


# ---------- хэш пароля ----------
def hash_password(password, n=2 ** 15, r=8, p=1):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt:{n}:{r}:{p}:{b64(salt)}:{b64(dk)}"


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, dk = stored.split(":")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=128 * 1024 * 1024, dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        log.exception("verify_password: bad hash format")
        return False


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- контекст запроса ----------
def client_ip():
    try:
        h = st.context.headers
        ip = h.get("X-Real-Ip") or h.get("X-Real-IP")
        if not isinstance(ip, str) or not ip:
            xff = h.get("X-Forwarded-For", "")
            ip = xff.split(",")[0].strip() if isinstance(xff, str) and xff else None
        if not isinstance(ip, str) or not ip:
            ip = st.context.ip_address
        return ip[:64] if isinstance(ip, str) and ip else "unknown"
    except Exception:
        return "unknown"


def _set_cookie_js(value, max_age):
    # Значение — urlsafe-токен, json.dumps экранирует его для JS.
    js = (f"document.cookie = {json.dumps(COOKIE_NAME)} + '=' + {json.dumps(value)} + "
          f"'; Max-Age={int(max_age)}; Path=/; Secure; SameSite=Strict';")
    st.html(f"<script>{js}</script>", unsafe_allow_javascript=True)


# ---------- публичное API ----------
def restore_session():
    """Пытается восстановить вход по cookie. Возвращает username или None."""
    try:
        token = st.context.cookies.get(COOKIE_NAME)
    except Exception:
        token = None
    if not isinstance(token, str) or not token or len(token) > 200:
        return None
    try:
        user = models.touch_auth_session(_token_hash(token), SESSION_DAYS)
    except Exception:
        log.exception("restore_session failed")
        return None
    if user:
        st.session_state.token_hash = _token_hash(token)
    return user


def is_locked(ip):
    by_ip, total = models.failed_attempts(ip, LOCK_WINDOW_MIN)
    return by_ip >= MAX_FAILS_PER_IP or total >= MAX_FAILS_TOTAL


def try_login(username, password):
    """Возвращает (ok: bool, message: str)."""
    ip = client_ip()
    if is_locked(ip):
        log.warning("login locked for ip=%s", ip)
        return False, f"Слишком много неудачных попыток. Попробуйте через {LOCK_WINDOW_MIN} минут."
    expected_user = os.environ.get("ADMIN_USER", "admin")
    stored = os.environ.get("ADMIN_PASS_HASH", "")
    ok = bool(stored) and hmac.compare_digest(username.encode(), expected_user.encode()) \
        and verify_password(password, stored)
    models.record_login_attempt(ip, username[:100], ok)
    if not ok:
        log.warning("login failed user=%r ip=%s", username[:50], ip)
        time.sleep(1.0)
        return False, "Неверный логин или пароль."
    token = secrets.token_urlsafe(32)
    models.create_auth_session(_token_hash(token), expected_user, ip, SESSION_DAYS)
    st.session_state.token_hash = _token_hash(token)
    st.session_state.pending_cookie = token
    log.info("login ok user=%s ip=%s", expected_user, ip)
    return True, ""


def flush_cookie():
    """Ставит cookie после успешного входа (вызывать после st.rerun)."""
    token = st.session_state.pop("pending_cookie", None)
    if token:
        _set_cookie_js(token, SESSION_DAYS * 86400)
    if st.session_state.pop("clear_cookie", False):
        _set_cookie_js("", 0)


def logout():
    th = st.session_state.get("token_hash")
    if th:
        try:
            models.delete_auth_session(th)
        except Exception:
            log.exception("logout delete failed")
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.session_state.clear_cookie = True
    st.session_state.logged_out = True
