"""Daily DB backup → GitHub Releases.

Triggered daily at 03:00 UTC by cron_worker (daily_backup_github_loop).
Can also be invoked manually: `python _backup_to_github.py`.

Flow:
  1. pg_dump main DB (RESALE_DATABASE_URL) → /tmp/resale_YYYY-MM-DD.sql
  2. pg_dump intel DB (INTELLIGENCE_DATABASE_URL) → /tmp/intel_YYYY-MM-DD.sql
  3. gzip both → .sql.gz
  4. Verify gzip CRC integrity via `gzip -t` equivalent.
  5. Create GitHub release `backup-YYYY-MM-DD` in repo $BACKUP_REPO
     (default: lesnykhvadim999-beep/vadim-db-backups).
  6. Upload .sql.gz files as release assets.
  7. Delete releases older than 30 days.
  8. admin_notify on success/failure.

Env:
  RESALE_DATABASE_URL       — DSN for main DB
  INTELLIGENCE_DATABASE_URL — DSN for intel DB (optional; skipped if absent)
  GITHUB_TOKEN / GH_TOKEN   — PAT with `repo` scope
  BACKUP_REPO               — owner/repo (default: lesnykhvadim999-beep/vadim-db-backups)
  BACKUP_DIR                — local tmp dir (default: tempfile.gettempdir())

Uses --no-owner --no-privileges --no-acl on pg_dump so dumps don't lock objects.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

try:
    from admin_notify import admin_notify
except Exception:
    try:
        sys.path.insert(0, r"C:\Projects\shared")
        from admin_notify import admin_notify  # type: ignore
    except Exception:
        def admin_notify(*a, **kw):  # type: ignore
            return False


# ── Config ──────────────────────────────────────────────────────────────────
RESALE_DB = os.environ.get("RESALE_DATABASE_URL", "").strip()
INTEL_DB = (os.environ.get("INTELLIGENCE_DATABASE_URL", "").strip()
            or os.environ.get("INTEL_DATABASE_URL", "").strip())
GH_TOKEN = (os.environ.get("GITHUB_TOKEN", "").strip()
            or os.environ.get("GH_TOKEN", "").strip())
BACKUP_REPO = os.environ.get(
    "BACKUP_REPO", "lesnykhvadim999-beep/vadim-db-backups"
).strip()
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or tempfile.gettempdir())
RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))

GH_API = "https://api.github.com"
USER_AGENT = "vadim-db-backup/1.0"


# ── HTTP helper ─────────────────────────────────────────────────────────────
def _gh_request(method: str, path: str, *, body: bytes | dict | None = None,
                extra_headers: dict | None = None,
                content_type: str = "application/json") -> tuple[int, bytes]:
    url = path if path.startswith("http") else f"{GH_API}{path}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if extra_headers:
        headers.update(extra_headers)
    data: bytes | None
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        data = body
        if data is not None:
            headers["Content-Type"] = content_type
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urlerror.HTTPError as e:
        return e.code, e.read()


# ── pg_dump + gzip ───────────────────────────────────────────────────────────
def _pg_dump(dsn: str, out_path: Path) -> bool:
    """Run pg_dump with options that don't lock the prod DB.

    --no-owner / --no-privileges / --no-acl produce portable plain SQL.
    --no-synchronized-snapshots is implied; we don't pass -j so no parallel locks.
    """
    cmd = [
        "pg_dump",
        "--no-owner", "--no-privileges", "--no-acl",
        "--format=plain",
        "--file", str(out_path),
        dsn,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0:
            print(f"[backup-gh] pg_dump failed rc={proc.returncode}: "
                  f"{(proc.stderr or '')[:500]}", flush=True)
            return False
        return True
    except FileNotFoundError:
        print("[backup-gh] pg_dump binary not in PATH", flush=True)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[backup-gh] pg_dump exception: {e}", flush=True)
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


def _verify_gzip(path: Path) -> bool:
    """Read whole gzip stream — raises BadGzipFile on CRC mismatch."""
    try:
        with gzip.open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[backup-gh] gzip verify FAILED for {path.name}: {e}", flush=True)
        return False


# ── GitHub release ──────────────────────────────────────────────────────────
def _ensure_repo() -> bool:
    """Verify backup repo exists. We don't auto-create — must be created once
    manually (private, empty)."""
    code, _ = _gh_request("GET", f"/repos/{BACKUP_REPO}")
    if code == 200:
        return True
    print(f"[backup-gh] repo {BACKUP_REPO} not accessible (HTTP {code}). "
          f"Create it manually (private) once.", flush=True)
    return False


def _create_release(tag: str) -> tuple[int | None, str | None]:
    """Create a release. Returns (release_id, upload_url_template)."""
    body = {
        "tag_name": tag,
        "name": tag,
        "body": f"Automated DB backup {tag}",
        "draft": False,
        "prerelease": False,
    }
    code, raw = _gh_request("POST", f"/repos/{BACKUP_REPO}/releases", body=body)
    if code in (200, 201):
        obj = json.loads(raw.decode("utf-8"))
        return obj.get("id"), obj.get("upload_url")
    # 422 = tag already exists — try fetching it
    if code == 422:
        code2, raw2 = _gh_request(
            "GET", f"/repos/{BACKUP_REPO}/releases/tags/{tag}")
        if code2 == 200:
            obj = json.loads(raw2.decode("utf-8"))
            return obj.get("id"), obj.get("upload_url")
    print(f"[backup-gh] create_release HTTP {code}: "
          f"{raw[:300].decode('utf-8', 'replace')}", flush=True)
    return None, None


def _upload_asset(upload_url_template: str, file_path: Path) -> bool:
    # GitHub upload URL is templated:  ...{?name,label}
    base = upload_url_template.split("{")[0]
    url = f"{base}?name={file_path.name}"
    try:
        data = file_path.read_bytes()
    except Exception as e:  # noqa: BLE001
        print(f"[backup-gh] cannot read {file_path}: {e}", flush=True)
        return False
    code, raw = _gh_request("POST", url, body=data,
                             content_type="application/gzip")
    if code in (200, 201):
        return True
    print(f"[backup-gh] upload {file_path.name} HTTP {code}: "
          f"{raw[:300].decode('utf-8', 'replace')}", flush=True)
    return False


def _cleanup_old_releases() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    code, raw = _gh_request(
        "GET", f"/repos/{BACKUP_REPO}/releases?per_page=100")
    if code != 200:
        print(f"[backup-gh] list releases HTTP {code}", flush=True)
        return 0
    try:
        releases = json.loads(raw.decode("utf-8"))
    except Exception:
        return 0
    removed = 0
    for rel in releases:
        try:
            tag = rel.get("tag_name") or ""
            if not tag.startswith("backup-"):
                continue
            created = rel.get("created_at") or ""
            # GitHub returns ISO8601 with trailing Z
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if ts < cutoff:
                rid = rel.get("id")
                if not rid:
                    continue
                c1, _ = _gh_request(
                    "DELETE", f"/repos/{BACKUP_REPO}/releases/{rid}")
                # also delete tag ref
                c2, _ = _gh_request(
                    "DELETE", f"/repos/{BACKUP_REPO}/git/refs/tags/{tag}")
                if c1 in (200, 204):
                    removed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[backup-gh] cleanup skip: {e}", flush=True)
    return removed


# ── Local retention ─────────────────────────────────────────────────────────
def _cleanup_local() -> int:
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    removed = 0
    for pat in ("resale_*.sql.gz", "intel_*.sql.gz",
                "resale_*.sql", "intel_*.sql"):
        for f in BACKUP_DIR.glob(pat):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
    return removed


# ── Main ────────────────────────────────────────────────────────────────────
def run() -> int:
    if not GH_TOKEN:
        admin_notify(
            "⚠️ <b>Daily GitHub backup skipped</b>\nGITHUB_TOKEN не задан",
            priority="warn")
        print("[backup-gh] GITHUB_TOKEN absent — exiting", flush=True)
        return 2
    if not RESALE_DB:
        admin_notify(
            "❌ <b>Daily backup FAILED</b>\nRESALE_DATABASE_URL не задан",
            priority="critical")
        return 1
    if not _ensure_repo():
        admin_notify(
            f"❌ <b>Daily backup FAILED</b>\nrepo {BACKUP_REPO} недоступен. "
            f"Создай его (private) и дай GITHUB_TOKEN scope=repo.",
            priority="critical")
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag = f"backup-{date_str}"

    artifacts: list[Path] = []
    errors: list[str] = []

    # main DB
    main_sql = BACKUP_DIR / f"resale_{date_str}.sql"
    print(f"[backup-gh] pg_dump main → {main_sql}", flush=True)
    if _pg_dump(RESALE_DB, main_sql):
        gz = _gzip(main_sql)
        if _verify_gzip(gz):
            artifacts.append(gz)
        else:
            errors.append("main gzip CRC failed")
    else:
        errors.append("main pg_dump failed")

    # intel DB (optional)
    if INTEL_DB:
        intel_sql = BACKUP_DIR / f"intel_{date_str}.sql"
        print(f"[backup-gh] pg_dump intel → {intel_sql}", flush=True)
        if _pg_dump(INTEL_DB, intel_sql):
            gz = _gzip(intel_sql)
            if _verify_gzip(gz):
                artifacts.append(gz)
            else:
                errors.append("intel gzip CRC failed")
        else:
            errors.append("intel pg_dump failed")
    else:
        print("[backup-gh] INTELLIGENCE_DATABASE_URL absent — skip intel",
              flush=True)

    if not artifacts:
        admin_notify(
            f"❌ <b>Daily backup FAILED</b>\nartifacts=0\nerrors: "
            + "; ".join(errors),
            priority="critical")
        return 1

    # release
    rel_id, upload_url = _create_release(tag)
    if not upload_url:
        admin_notify(
            f"❌ <b>Daily backup FAILED</b>\ncreate_release {tag} failed",
            priority="critical")
        return 1

    uploaded: list[str] = []
    for f in artifacts:
        if _upload_asset(upload_url, f):
            uploaded.append(f"{f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")
        else:
            errors.append(f"upload {f.name} failed")

    rel_removed = _cleanup_old_releases()
    local_removed = _cleanup_local()

    ok = bool(uploaded) and not any("failed" in e for e in errors)
    msg = (
        f"{'✅' if ok else '⚠️'} <b>Daily backup → GitHub</b>\n"
        f"tag: <code>{tag}</code>\n"
        f"uploaded: {len(uploaded)}\n"
        + ("\n".join(f"  • {u}" for u in uploaded) + "\n" if uploaded else "")
        + f"retention: removed {rel_removed} old releases, {local_removed} local\n"
        + (f"errors: {'; '.join(errors)}" if errors else "errors: none")
    )
    admin_notify(msg, priority="info" if ok else "warn")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
