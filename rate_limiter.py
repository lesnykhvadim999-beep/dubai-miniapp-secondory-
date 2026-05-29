"""
Per-user rate limiter (in-memory).

Использование:
    from rate_limiter import check_rate
    if not check_rate(uid):
        send(cid, "Слишком быстро, подожди.")
        return

Дизайн:
  • Хранилище: {user_id: deque[timestamps]} — sliding window 60 секунд.
  • check_rate(uid, max_per_minute=30) → True если разрешено.
  • Thread-safe: блокировка `threading.Lock` (resale-bot polling — single thread,
    но cron/Telethon работает в отдельном потоке).
  • Логирование превышений в admin_actions (audit_log) — best-effort.
"""
from __future__ import annotations

import time
import logging
import threading
from collections import defaultdict, deque
from typing import Deque, Dict

log = logging.getLogger("rate_limiter")

WINDOW_SECONDS = 60.0
DEFAULT_MAX_PER_MIN = 30

_buckets: Dict[int, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()
# чтобы не спамить admin-уведомлениями — не чаще 1 раз в 10 минут на uid
_last_alert: Dict[int, float] = {}
_ALERT_COOLDOWN = 600.0


def check_rate(user_id: int, max_per_minute: int = DEFAULT_MAX_PER_MIN) -> bool:
    """Возвращает True если запрос можно обработать, False если rate exceeded."""
    if user_id is None:
        return True
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS

    with _lock:
        bucket = _buckets[user_id]
        # удалить старые
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_per_minute:
            # exceeded
            alert_ts = _last_alert.get(user_id, 0)
            should_alert = (now - alert_ts) > _ALERT_COOLDOWN
            if should_alert:
                _last_alert[user_id] = now
        else:
            bucket.append(now)
            should_alert = False
            return True

    # лог + audit (вне блокировки)
    log.warning("rate exceeded uid=%s (%s/min)", user_id, max_per_minute)
    if should_alert:
        try:
            import sys
            sys.path.insert(0, r"C:\Projects\shared")
            from audit_log import audit_log  # type: ignore
            audit_log(
                user_id=user_id,
                action="rate_limit_exceeded",
                target=None,
                payload={"max_per_minute": max_per_minute,
                          "window": int(WINDOW_SECONDS)},
            )
        except Exception:
            pass
    return False


def reset_user(user_id: int) -> None:
    with _lock:
        _buckets.pop(user_id, None)
        _last_alert.pop(user_id, None)


def stats() -> dict:
    """Текущая нагрузка — кол-во активных пользователей и их хитов."""
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS
    out = {"active_users": 0, "total_hits_last_min": 0}
    with _lock:
        for uid, bucket in _buckets.items():
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if bucket:
                out["active_users"] += 1
                out["total_hits_last_min"] += len(bucket)
    return out
