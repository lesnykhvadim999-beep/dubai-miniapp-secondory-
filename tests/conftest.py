"""
conftest.py — базовые фикстуры и патчи для тестов wizard state machine resale-bot.

Ключевой подход:
- db_schema мокируется ещё ДО импорта resale_bot (sys.modules patch).
- _send, do_search, send_results патчатся через unittest.mock.patch.
- user_states и user_lang сбрасываются перед каждым тестом.
- Никакого реального Telegram / Railway / БД.
"""
import sys
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

# ── Мок db_schema (должен быть до импорта resale_bot) ────────────────────────
def _make_db_schema_mock():
    mod = types.ModuleType("db_schema")
    mod.init_db          = MagicMock(return_value=None)
    mod.search_listings  = MagicMock(return_value=([], 0))
    mod.get_listing_by_id = MagicMock(return_value=None)
    mod.get_listing_images = MagicMock(return_value=[])
    mod.get_price_history  = MagicMock(return_value=[])
    mod.save_user          = MagicMock(return_value=None)
    mod.save_lead          = MagicMock(return_value=None)
    mod.get_full_stats     = MagicMock(return_value={})
    mod.get_conn           = MagicMock(return_value=MagicMock())
    return mod


# Регистрируем мок до любого импорта resale_bot
if "db_schema" not in sys.modules:
    sys.modules["db_schema"] = _make_db_schema_mock()

# ── Импорт resale_bot (однократно, с подавлением side-effects) ───────────────
# Патчим requests и threading чтобы не было реальных HTTP/threads при импорте.
with patch("requests.post", return_value=MagicMock(json=lambda: {"ok": True})), \
     patch("requests.get",  return_value=MagicMock(json=lambda: {"ok": True})), \
     patch("threading.Thread", MagicMock()):
    import resale_bot as bot


# ── Helpers ───────────────────────────────────────────────────────────────────
UID = 100001   # тестовый user id
CID = 200001   # тестовый chat id


def reset_state(uid=UID):
    """Полный сброс состояния пользователя."""
    bot.user_states.pop(uid, None)
    bot.user_lang[uid] = "en"


def get_en(key):
    """Вернуть EN-перевод кнопки."""
    return bot.T["en"][key]


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clean_state():
    """Сбрасывает состояние до и после каждого теста."""
    reset_state()
    yield
    reset_state()


@pytest.fixture()
def mock_send(monkeypatch):
    """Патч _send — перехватывает все сообщения бота."""
    m = MagicMock()
    monkeypatch.setattr(bot, "_send", m)
    return m


@pytest.fixture()
def mock_search(monkeypatch):
    """Патч do_search + send_results — симулирует нулевой результат поиска."""
    ds = MagicMock()
    sr = MagicMock()
    monkeypatch.setattr(bot, "do_search", ds)
    monkeypatch.setattr(bot, "send_results", sr)
    return ds, sr


@pytest.fixture()
def mock_all(mock_send, mock_search):
    """Удобная комбинация — возвращает (mock_send, do_search, send_results)."""
    ds, sr = mock_search
    return mock_send, ds, sr
