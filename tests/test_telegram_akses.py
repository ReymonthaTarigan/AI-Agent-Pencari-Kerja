"""Bot hanya boleh melayani pemiliknya. Diuji tanpa koneksi ke Telegram."""

from dataclasses import replace

from telegram import Update, User

from app.config import muat_config
from app.integrasi import telegram_bot as tb

PEMILIK, ORANG_LAIN = 1111, 2222


def _update(app, chat_id: int, teks: str) -> Update:
    entities = [{"type": "bot_command", "offset": 0, "length": len(teks.split()[0])}] if teks.startswith("/") else []
    data = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 0,
            "text": teks,
            "entities": entities,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "x"},
        },
    }
    return Update.de_json(data, app.bot)


def _handler_terpilih(app, update):
    for h in app.handlers[0]:
        if h.check_update(update):
            return h.callback
    return None


def _app(owner):
    cfg = replace(muat_config(), telegram_token="123456:TOKEN-UJI", owner_chat_id=owner)
    app = tb.buat_aplikasi(cfg)
    # Biasanya diisi dari Telegram saat bot dinyalakan; di test diisi manual supaya tanpa jaringan.
    app.bot._bot_user = User(id=123456, is_bot=True, first_name="Uji", username="uji_bot")
    return app


def test_orang_lain_diabaikan_untuk_semua_pesan():
    app = _app(PEMILIK)
    for teks in ("/start", "/status", "/whoami", "halo"):
        assert _handler_terpilih(app, _update(app, ORANG_LAIN, teks)) is tb.abaikan_orang_lain


def test_pemilik_dilayani():
    app = _app(PEMILIK)
    assert _handler_terpilih(app, _update(app, PEMILIK, "/status")) is tb.cmd_status
    assert _handler_terpilih(app, _update(app, PEMILIK, "/start")) is tb.cmd_start
    assert _handler_terpilih(app, _update(app, PEMILIK, "halo")) is tb.balas_teks


def test_mode_setup_hanya_whoami():
    app = _app(None)
    assert _handler_terpilih(app, _update(app, ORANG_LAIN, "/whoami")) is tb.cmd_whoami
    assert _handler_terpilih(app, _update(app, ORANG_LAIN, "/status")) is tb.balas_mode_setup
