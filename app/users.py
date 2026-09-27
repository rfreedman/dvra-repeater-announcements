from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import USERS_PATH
from app.models import new_id

Role = Literal["admin", "readonly"]


class User(BaseModel):
    id: str = Field(default_factory=new_id)
    username: str
    password_hash: str
    role: Role = "readonly"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("username")
    @classmethod
    def username_present(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Username is required")
        if " " in name:
            raise ValueError("Username cannot contain spaces")
        if len(name) > 64:
            raise ValueError("Username is too long")
        return name

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at.isoformat(),
        }


class UsersDocument(BaseModel):
    version: Literal[1] = 1
    users: list[User] = Field(default_factory=list)


_lock = threading.Lock()
_store: UserStore | None = None


class UserStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> UsersDocument:
        if not self.path.exists():
            return UsersDocument()
        raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return UsersDocument()
        return UsersDocument.model_validate(raw)

    def _write(self, document: UsersDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document.model_dump(mode="json"), indent=2)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def document(self) -> UsersDocument:
        with _lock:
            return self._read()

    def count(self) -> int:
        return len(self.document().users)

    def list_users(self) -> list[User]:
        return list(self.document().users)

    def get(self, user_id: str) -> User | None:
        for item in self.document().users:
            if item.id == user_id:
                return item
        return None

    def by_username(self, username: str) -> User | None:
        key = username.strip().lower()
        for item in self.document().users:
            if item.username.lower() == key:
                return item
        return None

    def admin_count(self) -> int:
        return sum(1 for item in self.document().users if item.role == "admin")

    def create(self, *, username: str, password_hash: str, role: Role) -> User:
        with _lock:
            document = self._read()
            key = username.strip().lower()
            if any(item.username.lower() == key for item in document.users):
                raise ValueError("Username already exists")
            user = User(username=username, password_hash=password_hash, role=role)
            users = list(document.users)
            users.append(user)
            self._write(document.model_copy(update={"users": users}))
            return user

    def update(
        self,
        user_id: str,
        *,
        username: str | None = None,
        password_hash: str | None = None,
        role: Role | None = None,
    ) -> User:
        with _lock:
            document = self._read()
            items = list(document.users)
            found: User | None = None
            index = -1
            for i, item in enumerate(items):
                if item.id == user_id:
                    found = item
                    index = i
                    break
            if found is None:
                raise KeyError(user_id)

            updates: dict[str, object] = {}
            if username is not None:
                key = username.strip().lower()
                if any(item.username.lower() == key and item.id != user_id for item in items):
                    raise ValueError("Username already exists")
                updates["username"] = username
            if password_hash is not None:
                updates["password_hash"] = password_hash
            if role is not None:
                if found.role == "admin" and role != "admin":
                    admins = sum(1 for item in items if item.role == "admin")
                    if admins <= 1:
                        raise ValueError("Cannot demote the last admin")
                updates["role"] = role

            updated = found.model_copy(update=updates)
            items[index] = updated
            self._write(document.model_copy(update={"users": items}))
            return updated

    def delete(self, user_id: str) -> bool:
        with _lock:
            document = self._read()
            target = next((item for item in document.users if item.id == user_id), None)
            if target is None:
                return False
            if target.role == "admin":
                admins = sum(1 for item in document.users if item.role == "admin")
                if admins <= 1:
                    raise ValueError("Cannot delete the last admin")
            kept = [item for item in document.users if item.id != user_id]
            self._write(document.model_copy(update={"users": kept}))
            return True


def get_user_store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore(USERS_PATH)
    return _store


def set_user_store(store: UserStore | None) -> None:
    global _store
    _store = store
