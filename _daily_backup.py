"""
Daily backup → Cloudflare R2 (или локально, если R2 env не заданы).

Запуск (cron / scheduler):
    python _daily_backup.py

Поток:
  1. pg_dump resale DB → /tmp/listings_YYYYMMDD.sql
  2. gzip → /tmp/listings_YYYYMMDD.sql.gz
  3. Если есть R2_ENDPOINT + R2_KEY_ID + R2_SECRET + R2_BUCKET → upload boto3.
  4. Иначе — оставляем локально и шлём admin_notify(priority='info').
  5. Retention: чистим локальные файлы listings_*.sql.gz старше 30 дней.

Env:
  RESALE_DATABASE_URL — DSN для pg_dump (обязательно)
  R2_ENDPOINT  — https://<account>.r2.cloudflarestorage.com
  R2_KEY_ID    — Access Key ID
  R2_SECRET    — Secret Access Key
  R2_BUCKET    — bucket name
  BACKUP_DIR   — куда складывать (default: /tmp на Linux, %TEMP% на Windows)
"""
from __future__ import annotations

import os
import sys
import gzip
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# admin_notify (shared/)
try:
    from admin_notify import admin_notify
except Exception:
    try:
        sys.path.insert(0, r"C:\Projects\shared")
        from admin_notify import admin_notify  # type: ignore
    except Exception:
        def admin_notify(*a, **kw):  # type: ignore
            return False


RESALE_DB = os.environ.get(
    "RESALE_DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV",
)
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or tempfile.gettempdir())
RETENTION_DAYS = 30


def _pg_dump(out_path: Path) -> bool:
    cmd = ["pg_dump", "--no-owner", "--no-privileges", "--format=plain",
           "--file", str(out_path), RESALE_DB]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            print(f"[backup] pg_dump failed: {proc.stderr[:500]}")
            return False
        return True
    except FileNotFoundError:
        print("[backup] pg_dump binary not found in PATH")
        return False
    except Exception as e:
        print(f"[backup] pg_dump exception: {e}")
        return False


def _gzip(src: Path) -> Path:
    dst = src.with_suffix(src.suffix + ".gz")
    with open(src, "rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    try:
        src.unlink()
    except Exception:
        pass
    return dst


def _upload_r2(path: Path) -> bool:
    endpoint = os.environ.get("R2_ENDPOINT", "").strip()
    key = os.environ.get("R2_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET", "").strip()
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not (endpoint and key and secret and bucket):
        return False
    try:
        import boto3  # type: ignore
    except Exception:
        print("[backup] boto3 not installed")
        return False
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
        )
        s3.upload_file(str(path), bucket, f"backups/{path.name}")
        return True
    except Exception as e:
        print(f"[backup] R2 upload failed: {e}")
        return False


def _cleanup_old() -> int:
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    removed = 0
    try:
        for f in BACKUP_DIR.glob("listings_*.sql.gz"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def run() -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    sql_path = BACKUP_DIR / f"listings_{date_str}.sql"

    print(f"[backup] dumping → {sql_path}")
    if not _pg_dump(sql_path):
        admin_notify(
            f"❌ <b>Daily backup FAILED</b>\npg_dump не сработал ({sql_path.name})",
            priority="critical",
        )
        return 1

    gz_path = _gzip(sql_path)
    size_mb = gz_path.stat().st_size / 1024 / 1024
    print(f"[backup] gzip done: {gz_path} ({size_mb:.1f} MB)")

    uploaded = _upload_r2(gz_path)
    cleaned = _cleanup_old()

    if uploaded:
        admin_notify(
            f"✅ <b>Daily backup</b>\nfile: <code>{gz_path.name}</code>\n"
            f"size: {size_mb:.1f} MB\nR2: uploaded\nretention: removed {cleaned} old",
            priority="info",
        )
    else:
        admin_notify(
            f"💾 <b>Daily backup (local only)</b>\n"
            f"file: <code>{gz_path}</code>\nsize: {size_mb:.1f} MB\n"
            f"R2 не настроен (env R2_ENDPOINT/R2_KEY_ID/R2_SECRET/R2_BUCKET)\n"
            f"retention: removed {cleaned} old",
            priority="info",
        )
    return 0


if __name__ == "__main__":
    sys.exit(run())
