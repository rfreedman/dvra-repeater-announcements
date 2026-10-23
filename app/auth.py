from __future__ import annotations

import threading
import time
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, Request

from app.config import SESSION_IDLE_SECONDS, login_max_failures, login_window_seconds
from app.users import Role, User, get_user_store

SESSION_USER_KEY = "user_id"
SESSION_LAST_ACTIVE_KEY = "last_active"
MIN_PASSWORD_LEN = 8
# The UI sends this on timer-driven refreshes; they must not count as activity,
# or an open tab would never reach the idle timeout. Only its presence matters; the
# value is ignored. static/app.js (BACKGROUND_HEADERS) must use the same name.
BACKGROUND_POLL_HEADER = "X-Background-Poll"


class LoginRateLimiter:
    """Lock out a client+username after repeated failed sign-ins (in memory)."""

    def __init__(self, *, max_failures: int = 5, window_seconds: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, key: str) -> float:
        now = time.time()
        with self._lock:
            recent = self._recent(key, now)
            if len(recent) < self.max_failures:
                return 0.0
            return max(0.0, self.window_seconds - (now - recent[0]))

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._failures[key] = [*self._recent(key, now), now]

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


def configured_login_limiter() -> LoginRateLimiter:
    """Build a limiter from ``TTS_LOGIN_MAX_FAILURES`` / ``TTS_LOGIN_WINDOW_SECONDS``."""
    return LoginRateLimiter(
        max_failures=login_max_failures(),
        window_seconds=login_window_seconds(),
    )


login_limiter = configured_login_limiter()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    return password


def set_session_user(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = user.id
    request.session[SESSION_LAST_ACTIVE_KEY] = time.time()


def clear_session(request: Request) -> None:
    request.session.clear()


def _idle_expired(request: Request) -> bool:
    raw: str | None = request.session.get(SESSION_LAST_ACTIVE_KEY)
    if raw is None:
        return True
    try:
        last_active = float(raw)
    except (TypeError, ValueError):
        return True
    return (time.time() - last_active) > SESSION_IDLE_SECONDS


def touch_session(request: Request) -> None:
    request.session[SESSION_LAST_ACTIVE_KEY] = time.time()


def current_user_optional(request: Request) -> User | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id or not isinstance(user_id, str):
        return None
    if _idle_expired(request):
        clear_session(request)
        return None
    user = get_user_store().get(user_id)
    if user is None:
        clear_session(request)
        return None
    if BACKGROUND_POLL_HEADER not in request.headers:
        touch_session(request)
    return user


def require_user(request: Request) -> User:
    user = current_user_optional(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def authenticate(username: str, password: str) -> User | None:
    user = get_user_store().by_username(username)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def is_admin(role: Role | str) -> bool:
    return role == "admin"
