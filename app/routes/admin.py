"""Admin-only environment history and logo / favicon uploads."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from app.admin_config import (
    ConfigState,
    RuntimeConfigValues,
    bump_config_generation,
    clean_long,
    clean_short,
    config_state,
    ensure_history,
    revert_config,
    save_config,
)
from app.branding import (
    MAX_BRANDING_BYTES,
    clear_favicon,
    clear_logo,
    resolve_favicon,
    resolve_logo,
    save_favicon,
    save_logo,
    save_logo_display,
)
from app.deps import AdminUser

router = APIRouter()

_CACHE = {"Cache-Control": "no-cache"}


class ConfigSaveIn(BaseModel):
    values: RuntimeConfigValues
    short_description: str = ""
    long_description: str = ""


class ConfigRevertIn(BaseModel):
    id: str


class BrandingResult(BaseModel):
    ok: bool
    generation: int


class LogoDisplayIn(BaseModel):
    width: int
    height: int


def _validation_detail(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid configuration"
    message = errors[0].get("msg", "Invalid configuration")
    text = str(message)
    prefix = "Value error, "
    if text.startswith(prefix):
        return text[len(prefix) :]
    return text


@router.get("/api/admin/config")
def get_admin_config(_admin: AdminUser) -> ConfigState:
    return config_state(ensure_history(), changed=False)


@router.post("/api/admin/config")
def post_admin_config(body: ConfigSaveIn, _admin: AdminUser) -> ConfigState:
    try:
        short = clean_short(body.short_description)
        long = clean_long(body.long_description)
        document = save_config(body.values, short, long)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return config_state(document, changed=True)


@router.post("/api/admin/config/revert")
def post_admin_revert(body: ConfigRevertIn, _admin: AdminUser) -> ConfigState:
    try:
        document, changed = revert_config(body.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Saved configuration not found") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return config_state(document, changed=changed)


@router.get("/branding/logo")
def get_logo() -> FileResponse:
    path, media = resolve_logo()
    return FileResponse(path, media_type=media, headers=_CACHE)


@router.get("/branding/favicon")
def get_favicon() -> FileResponse:
    path, media = resolve_favicon()
    return FileResponse(path, media_type=media, headers=_CACHE)


@router.post("/api/admin/branding/display")
def post_logo_display(body: LogoDisplayIn, _admin: AdminUser) -> BrandingResult:
    try:
        _ = save_logo_display(body.width, body.height)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc
    return BrandingResult(ok=True, generation=bump_config_generation())


@router.post("/api/admin/branding/logo")
async def upload_logo(
    _admin: AdminUser,
    file: Annotated[UploadFile, File()],
    also_favicon: Annotated[str, Form()] = "true",
) -> BrandingResult:
    data = await _read_upload(file)
    try:
        save_logo(data, also_favicon=_form_flag(also_favicon))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BrandingResult(ok=True, generation=bump_config_generation())


@router.post("/api/admin/branding/favicon")
async def upload_favicon(
    _admin: AdminUser,
    file: Annotated[UploadFile, File()],
) -> BrandingResult:
    data = await _read_upload(file)
    try:
        save_favicon(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BrandingResult(ok=True, generation=bump_config_generation())


@router.delete("/api/admin/branding/logo")
def delete_logo(_admin: AdminUser) -> BrandingResult:
    clear_logo()
    return BrandingResult(ok=True, generation=bump_config_generation())


@router.delete("/api/admin/branding/favicon")
def delete_favicon(_admin: AdminUser) -> BrandingResult:
    clear_favicon()
    return BrandingResult(ok=True, generation=bump_config_generation())


def _form_flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_BRANDING_BYTES + 1)
    if len(data) > MAX_BRANDING_BYTES:
        raise HTTPException(status_code=400, detail="File is larger than 2 MB")
    return data
