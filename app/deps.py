"""Shared FastAPI dependencies for authenticated routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth import require_admin, require_user
from app.users import User

AdminUser = Annotated[User, Depends(require_admin)]
AuthUser = Annotated[User, Depends(require_user)]
