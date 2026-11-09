"""Logo and favicon files. Uploads live under the data directory; defaults stay in static/."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import ClassVar

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import DATA_DIR, STATIC_DIR
from app.json_io import DataFileError, read_versioned_json, write_json_atomic

MAX_BRANDING_BYTES = 2 * 1024 * 1024
LOGO_MIN_PX = 64
LOGO_MAX_PX = 2048
FAVICON_MIN_PX = 16
FAVICON_MAX_PX = 256
FAVICON_EDGE_PX = 32
LOGO_DISPLAY_MIN = 16
LOGO_DISPLAY_MAX = 256
DEFAULT_LOGO_DISPLAY = 48
_DISPLAY_VERSION = 1

_LOGO_FORMATS = {"PNG", "JPEG", "WEBP"}
_FAVICON_FORMATS = {"PNG", "ICO"}
_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "ICO": ".ico"}
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}


class LogoDisplay(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    width: int = Field(ge=LOGO_DISPLAY_MIN, le=LOGO_DISPLAY_MAX)
    height: int = Field(ge=LOGO_DISPLAY_MIN, le=LOGO_DISPLAY_MAX)


def branding_dir() -> Path:
    override = os.environ.get("TTS_BRANDING_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return DATA_DIR / "branding"


def display_path() -> Path:
    return branding_dir() / "display.json"


def logo_display() -> LogoDisplay:
    path = display_path()
    raw = read_versioned_json(path, version=_DISPLAY_VERSION, kind="logo display")
    if raw is None:
        return LogoDisplay(width=DEFAULT_LOGO_DISPLAY, height=DEFAULT_LOGO_DISPLAY)
    try:
        return LogoDisplay.model_validate(raw)
    except ValidationError as exc:
        raise DataFileError(f"{path} has an invalid logo display size.") from exc


def logo_pixel_size() -> tuple[int, int]:
    path, _media = resolve_logo()
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, UnidentifiedImageError, ValueError):
        display = logo_display()
        return display.width, display.height


def save_logo_display(width: int, height: int) -> LogoDisplay:
    display = LogoDisplay(width=width, height=height)
    write_json_atomic(
        display_path(),
        {"version": _DISPLAY_VERSION, "width": display.width, "height": display.height},
    )
    return display


def resolve_logo() -> tuple[Path, str]:
    found = _uploaded("logo")
    if found is not None:
        return found
    return STATIC_DIR / "logo.png", "image/png"


def resolve_favicon() -> tuple[Path, str]:
    found = _uploaded("favicon")
    if found is not None:
        return found
    return STATIC_DIR / "favicon.ico", "image/x-icon"


def save_logo(data: bytes, *, also_favicon: bool) -> None:
    image = _open_image(data)
    fmt = _require_format(image, _LOGO_FORMATS, "Logo must be a PNG, JPEG, or WebP image")
    _require_dimensions(
        image,
        low=LOGO_MIN_PX,
        high=LOGO_MAX_PX,
        message=(
            f"Logo must be between {LOGO_MIN_PX}×{LOGO_MIN_PX} and {LOGO_MAX_PX}×{LOGO_MAX_PX} pixels"
        ),
    )
    directory = branding_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _clear(directory, "logo")
    _ = (directory / f"logo{_EXTENSIONS[fmt]}").write_bytes(data)
    if also_favicon:
        _write_favicon_from(directory, image)


def save_favicon(data: bytes) -> None:
    image = _open_image(data)
    fmt = _require_format(image, _FAVICON_FORMATS, "Favicon must be a PNG or ICO image")
    _require_dimensions(
        image,
        low=FAVICON_MIN_PX,
        high=FAVICON_MAX_PX,
        message=(
            f"Favicon must be between {FAVICON_MIN_PX}×{FAVICON_MIN_PX} "
            f"and {FAVICON_MAX_PX}×{FAVICON_MAX_PX} pixels"
        ),
    )
    directory = branding_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _clear(directory, "favicon")
    _ = (directory / f"favicon{_EXTENSIONS[fmt]}").write_bytes(data)


def clear_logo() -> None:
    _clear(branding_dir(), "logo")


def clear_favicon() -> None:
    _clear(branding_dir(), "favicon")


def _write_favicon_from(directory: Path, source: Image.Image) -> None:
    width, height = source.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    square = source.crop((left, top, left + side, top + side)).convert("RGBA")
    icon = square.resize((FAVICON_EDGE_PX, FAVICON_EDGE_PX), Image.Resampling.LANCZOS)
    _clear(directory, "favicon")
    icon.save(directory / "favicon.png", format="PNG")


def _open_image(data: bytes) -> Image.Image:
    if len(data) > MAX_BRANDING_BYTES:
        raise ValueError("File is larger than 2 MB")
    if not data:
        raise ValueError("File is empty")
    try:
        image = Image.open(BytesIO(data))
        _ = image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("File is not a supported image") from exc
    return image


def _require_format(image: Image.Image, allowed: set[str], message: str) -> str:
    fmt = image.format or ""
    if fmt not in allowed:
        raise ValueError(message)
    return fmt


def _require_dimensions(image: Image.Image, *, low: int, high: int, message: str) -> None:
    width, height = image.size
    if width < low or height < low or width > high or height > high:
        raise ValueError(message)


def _uploaded(stem: str) -> tuple[Path, str] | None:
    directory = branding_dir()
    if not directory.is_dir():
        return None
    matches = sorted(path for path in directory.glob(f"{stem}.*") if path.is_file())
    if not matches:
        return None
    path = matches[0]
    return path, _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _clear(directory: Path, stem: str) -> None:
    if not directory.is_dir():
        return
    for path in directory.glob(f"{stem}.*"):
        if path.is_file():
            path.unlink()
