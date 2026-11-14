#!/usr/bin/env python3

"""Create a local backup archive and upload it to Cloudflare R2.

Cron example:
  cd /path/to/dvra-repeater-announcements && uv run python scripts/backup-to-r2.py

Credentials and notify settings live in project-root .env.secrets (gitignored, not archived).
Logs append to backup/backup.log (and stderr).
"""

from __future__ import annotations

import html
import logging
import os
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import boto3
from bird import APIError, Bird
from botocore.exceptions import ClientError

ROOT_DIR = Path(__file__).resolve().parents[1]
SECRETS_ENV_PATH = ROOT_DIR / ".env.secrets"
BACKUP_DIR = ROOT_DIR / "backup"
LOG_PATH = BACKUP_DIR / "backup.log"
DEFAULT_BACKUP_KEEP = 14
DEFAULT_NOTIFY_FROM_NAME = "Repeater Announcements"
BACKUP_TARGETS = (".env", "data", "config")
ARCHIVE_GLOB = "dvra-repeater-backup_*.tar.gz"

logger = logging.getLogger("backup_to_r2")


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required (set it in {SECRETS_ENV_PATH.name})")
    return value


def _backup_keep() -> int:
    raw = os.environ.get("R2_BACKUP_KEEP", "").strip()
    if not raw:
        return DEFAULT_BACKUP_KEEP
    try:
        keep = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"R2_BACKUP_KEEP must be an integer, got {raw!r}") from exc
    if keep < 1:
        raise RuntimeError(f"R2_BACKUP_KEEP must be >= 1, got {keep}")
    return keep


def setup_logging() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)


def load_r2_config() -> tuple[str, str, str, str, int]:
    """Load R2 settings from .env.secrets / the environment."""
    _load_dotenv(SECRETS_ENV_PATH)
    return (
        _require_env("R2_ACCOUNT_ID"),
        _require_env("R2_ACCESS_KEY_ID"),
        _require_env("R2_SECRET_ACCESS_KEY"),
        _require_env("R2_BUCKET_NAME"),
        _backup_keep(),
    )


def notify_admins(subject: str, body: str) -> None:
    """Email admins via Bird. Never raises; notify failures are logged only."""
    api_key = os.environ.get("BIRD_API_KEY", "").strip()
    sender = os.environ.get("BACKUP_NOTIFY_FROM", "").strip()
    from_name = (
        os.environ.get("BACKUP_NOTIFY_FROM_NAME", "").strip() or DEFAULT_NOTIFY_FROM_NAME
    )
    recipients = [
        addr.strip()
        for addr in os.environ.get("BACKUP_NOTIFY_TO", "").split(",")
        if addr.strip()
    ]
    if not api_key:
        logger.error("Skipping notify: BIRD_API_KEY is not set in %s", SECRETS_ENV_PATH.name)
        return
    if not sender:
        logger.error(
            "Skipping notify: BACKUP_NOTIFY_FROM is not set in %s", SECRETS_ENV_PATH.name
        )
        return
    if not recipients:
        logger.error(
            "Skipping notify: BACKUP_NOTIFY_TO is not set in %s", SECRETS_ENV_PATH.name
        )
        return

    html_body = "<p>" + html.escape(body).replace("\n", "<br>\n") + "</p>"
    try:
        with Bird() as client:
            message = client.email.send(
                from_={"email": sender, "name": from_name},
                to=recipients,
                subject=subject,
                text=body,
                html=html_body,
            )
        logger.info(
            "Notify email sent (id=%s status=%s) to %s",
            getattr(message, "id", "?"),
            getattr(message, "status", "?"),
            ", ".join(recipients),
        )
    except APIError as exc:
        logger.error("Bird email request failed: %s", exc)
    except Exception as exc:
        logger.exception("Bird email notify failed: %s", exc)


def create_backup_archive() -> Path:
    """Create a gzipped tar of .env, data/, and config/ under backup/."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_path = BACKUP_DIR / f"dvra-repeater-backup_{timestamp}.tar.gz"

    existing: list[tuple[Path, str]] = []
    for name in BACKUP_TARGETS:
        path = ROOT_DIR / name
        if path.exists():
            existing.append((path, name))
        else:
            logger.warning("Backup target missing, skipping: %s", path)

    if not existing:
        raise FileNotFoundError(
            f"No backup targets found under {ROOT_DIR} ({', '.join(BACKUP_TARGETS)})"
        )

    logger.info("Creating archive %s", archive_path)
    with tarfile.open(archive_path, "w:gz") as tar:
        for path, arcname in existing:
            tar.add(path, arcname=arcname)
    logger.info("Archive created (%s bytes)", archive_path.stat().st_size)
    return archive_path


def get_r2_client(account_id: str, access_key_id: str, secret_access_key: str):
    """Initialize and return a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",  # Required by boto3, ignored by R2
    )


def upload_backup(s3_client, bucket_name: str, archive_path: Path) -> None:
    """Upload a local archive to R2 using the archive basename as the object key."""
    object_key = archive_path.name
    logger.info("Uploading %s to r2://%s/%s", archive_path, bucket_name, object_key)
    s3_client.upload_file(str(archive_path), bucket_name, object_key)
    logger.info("Upload complete")


def prune_remote_backups(s3_client, bucket_name: str, max_files: int) -> None:
    """Delete oldest R2 objects beyond max_files (newest kept)."""
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket_name)

    objects = []
    for page in pages:
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("/"):
                objects.append(obj)

    if len(objects) <= max_files:
        logger.info(
            "Remote backups (%s) within limit (%s); nothing to delete",
            len(objects),
            max_files,
        )
        return

    objects.sort(key=lambda x: x["LastModified"], reverse=True)
    files_to_delete = objects[max_files:]
    delete_payload = {"Objects": [{"Key": obj["Key"]} for obj in files_to_delete]}

    logger.info("Deleting %s old remote backup(s)", len(files_to_delete))
    s3_client.delete_objects(Bucket=bucket_name, Delete=delete_payload)
    for obj in files_to_delete:
        logger.info("Deleted remote: %s", obj["Key"])


def prune_local_backups(max_files: int) -> None:
    """Delete oldest local archives beyond max_files (newest kept)."""
    archives = sorted(
        BACKUP_DIR.glob(ARCHIVE_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if len(archives) <= max_files:
        logger.info(
            "Local backups (%s) within limit (%s); nothing to delete",
            len(archives),
            max_files,
        )
        return

    for path in archives[max_files:]:
        path.unlink()
        logger.info("Deleted local: %s", path.name)


def main() -> int:
    setup_logging()
    _load_dotenv(SECRETS_ENV_PATH)
    try:
        account_id, access_key_id, secret_access_key, bucket_name, keep = load_r2_config()
        archive_path = create_backup_archive()
        client = get_r2_client(account_id, access_key_id, secret_access_key)
        upload_backup(client, bucket_name, archive_path)
        prune_remote_backups(client, bucket_name, keep)
        prune_local_backups(keep)
        logger.info("Backup finished successfully (keep=%s)", keep)
        size = archive_path.stat().st_size
        notify_admins(
            "Repeater backup succeeded",
            (
                f"Backup uploaded successfully.\n"
                f"Archive: {archive_path.name}\n"
                f"Size: {size} bytes\n"
                f"Bucket: {bucket_name}\n"
                f"Keep: {keep}"
            ),
        )
        return 0
    except ClientError as exc:
        logger.error("Cloudflare R2 API error: %s", exc)
        notify_admins("Repeater backup FAILED", f"Cloudflare R2 API error:\n{exc}")
        return 1
    except Exception as exc:
        logger.exception("Backup process failed: %s", exc)
        notify_admins("Repeater backup FAILED", f"Backup process failed:\n{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
