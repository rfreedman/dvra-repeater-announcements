"""Sign-in and user management routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.auth import (
    authenticate,
    clear_session,
    current_user_optional,
    hash_password,
    login_limiter,
    set_session_user,
    validate_password,
)
from app.deps import AdminUser, AuthUser
from app.schemas import LoginIn, SetupIn, UserCreateIn, UserUpdateIn
from app.users import get_user_store

router = APIRouter()


@router.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, object]:
    store = get_user_store()
    if store.count() == 0:
        return {"authenticated": False, "needs_setup": True, "user": None}
    user = current_user_optional(request)
    if user is None:
        return {"authenticated": False, "needs_setup": False, "user": None}
    return {"authenticated": True, "needs_setup": False, "user": user.public()}


@router.post("/api/auth/setup")
def auth_setup(req: SetupIn, request: Request) -> dict[str, object]:
    store = get_user_store()
    if store.count() != 0:
        raise HTTPException(status_code=409, detail="Setup already completed")
    try:
        password = validate_password(req.password)
        user = store.create(
            username=req.username,
            password_hash=hash_password(password),
            role="admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_session_user(request, user)
    return {"user": user.public()}


@router.post("/api/auth/login")
def auth_login(req: LoginIn, request: Request) -> dict[str, object]:
    if get_user_store().count() == 0:
        raise HTTPException(status_code=409, detail="Setup required")
    client = request.client.host if request.client else "unknown"
    limiter_key = f"{client}:{req.username.strip().lower()}"
    retry_after = login_limiter.retry_after(limiter_key)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed sign-ins. Try again in {int(retry_after) + 1} seconds.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    user = authenticate(req.username, req.password)
    if user is None:
        login_limiter.record_failure(limiter_key)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    login_limiter.reset(limiter_key)
    set_session_user(request, user)
    return {"user": user.public()}


@router.post("/api/auth/logout")
def auth_logout(request: Request) -> dict[str, bool]:
    clear_session(request)
    return {"ok": True}


@router.get("/api/auth/me")
def auth_me(user: AuthUser) -> dict[str, object]:
    return {"user": user.public()}


@router.get("/api/users")
def list_users(_admin: AdminUser) -> dict[str, object]:
    users = sorted(get_user_store().list_users(), key=lambda item: item.username.lower())
    return {"users": [item.public() for item in users]}


@router.post("/api/users")
def create_user(req: UserCreateIn, _admin: AdminUser) -> dict[str, object]:
    try:
        password = validate_password(req.password)
        user = get_user_store().create(
            username=req.username,
            password_hash=hash_password(password),
            role=req.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user.public()


@router.put("/api/users/{user_id}")
def update_user(user_id: str, req: UserUpdateIn, _admin: AdminUser) -> dict[str, object]:
    store = get_user_store()
    if store.get(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        password_hash = None
        if req.password is not None:
            password_hash = hash_password(validate_password(req.password))
        user = store.update(
            user_id,
            username=req.username,
            password_hash=password_hash,
            role=req.role,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user.public()


@router.delete("/api/users/{user_id}")
def delete_user(user_id: str, _admin: AdminUser) -> dict[str, bool]:
    try:
        if not get_user_store().delete(user_id):
            raise HTTPException(status_code=404, detail="User not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
