"""Titik masuk aplikasi: menjalankan bot Telegram.

Jalankan dari folder proyek:
    python -m app.main
Hentikan dengan Ctrl+C.
"""

from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.error import InvalidToken, NetworkError

from app.config import get_config
from app.db import repo
from app.integrasi.telegram_bot import buat_aplikasi


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Library HTTP mencatat URL lengkap (berisi token bot) di level INFO; turunkan supaya token tidak bocor ke log.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = get_config()
    if not cfg.telegram_token:
        print("TELEGRAM_BOT_TOKEN belum diisi di file .env")
        return 1

    conn = repo.connect(cfg.db_path)
    repo.init_db(conn)
    conn.close()

    app = buat_aplikasi(cfg)
    logging.getLogger(__name__).info("Bot berjalan. Tekan Ctrl+C untuk berhenti.")
    try:
        # Coba menghubungi Telegram sampai 3 kali sebelum menyerah.
        app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=3)
    except InvalidToken:
        print("\n[GAGAL] Token bot ditolak Telegram. Salin ulang TELEGRAM_BOT_TOKEN dari @BotFather.")
        return 1
    except NetworkError as e:  # termasuk TimedOut
        print(
            f"\n[GAGAL] Tidak bisa terhubung ke server Telegram ({type(e).__name__}).\n"
            "Kemungkinan jaringanmu lambat atau memblokir api.telegram.org.\n"
            "Coba: buka https://api.telegram.org di browser, ganti jaringan (misalnya hotspot HP),\n"
            "atau isi TELEGRAM_PROXY di .env bila memakai proxy."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
