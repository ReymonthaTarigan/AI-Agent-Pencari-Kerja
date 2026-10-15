"""Bot Telegram: pusat kendali agent.

Aturan akses (ditegakkan kode, bukan prompt):
- Selama TELEGRAM_OWNER_CHAT_ID kosong, bot dalam MODE SETUP: hanya /whoami yang dibalas,
  supaya kamu bisa mengetahui chat_id milikmu.
- Setelah TELEGRAM_OWNER_CHAT_ID diisi, bot HANYA melayani chat_id itu. Pesan dari orang lain
  tidak dibalas sama sekali, hanya dicatat di log.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

from app.config import Config
from app.db import repo
from app.llm.client import LLMClient

log = logging.getLogger(__name__)

BANTUAN = (
    "Perintah yang tersedia:\n"
    "/status - ringkasan profil dan pemakaian kuota LLM\n"
    "/bantuan - daftar perintah\n\n"
    "Fitur menilai lowongan, menyiapkan lamaran, dan lainnya ditambahkan mulai Tahap 1."
)


def _cfg(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["cfg"]


# ----- mode setup -----


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    log.info("whoami dari chat_id=%s", chat_id)
    await update.effective_message.reply_text(
        f"chat_id kamu: {chat_id}\n\nIsi angka ini ke TELEGRAM_OWNER_CHAT_ID di file .env, lalu jalankan ulang bot."
    )


async def balas_mode_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("Bot dalam mode setup. Kirim /whoami untuk melihat chat_id kamu.")


# ----- mode normal (hanya pemilik) -----


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    conn = repo.connect(cfg.db_path)
    try:
        repo.init_db(conn)
        repo.set_pengaturan(conn, "owner_terverifikasi_pada", datetime.now(UTC).isoformat())
    finally:
        conn.close()
    await update.effective_message.reply_text("Halo! Agent asisten cari kerja siap.\n\n" + BANTUAN)


async def cmd_bantuan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(BANTUAN)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    conn = repo.connect(cfg.db_path)
    try:
        repo.init_db(conn)
        s = repo.statistik_profil(conn)
        kuota = LLMClient(cfg, conn).status_kuota()
    finally:
        conn.close()

    baris = [
        "Profil",
        f"- {s['item']} item ({s['item_lengkap']} lengkap dengan bukti)",
        f"- {s['skill']} skill",
        "",
        "Kuota LLM (24 jam terakhir)",
    ]
    if not kuota:
        baris.append("- belum ada model di .env")
    for k in kuota:
        tanda = " (PERINGATAN)" if k["dipakai"] >= k["batas"] * cfg.kuota_peringatan else ""
        baris.append(f"- {k['penyedia']} ({k['model']}): {k['dipakai']}/{k['batas']}{tanda}")
    if "data_dummy" in str(cfg.profil_path):
        baris += ["", "Catatan: masih memakai profil dummy."]
    await update.effective_message.reply_text("\n".join(baris))


async def balas_teks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Pesan diterima. Di Tahap 0 bot baru mengenal perintah /status dan /bantuan."
    )


async def abaikan_orang_lain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    log.warning("Pesan dari chat_id=%s diabaikan (bukan pemilik).", chat.id if chat else "?")


async def tangani_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Error saat memproses update", exc_info=context.error)


# ----- diagnosis -----


async def catat_masuk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catat setiap pesan yang masuk (hanya perintahnya, bukan isi pesan biasa)."""
    chat = update.effective_chat
    pesan = update.effective_message
    teks = pesan.text if pesan and pesan.text else ""
    jenis = teks.split()[0] if teks.startswith("/") else ("teks" if teks else "lainnya")
    log.info("Pesan masuk dari chat_id=%s: %s", chat.id if chat else "?", jenis)


async def saat_terhubung(app: Application) -> None:
    owner = app.bot_data["cfg"].owner_chat_id
    log.info("Terhubung ke Telegram sebagai @%s", app.bot.username)
    log.info("Mode: %s", "SETUP (hanya /whoami)" if owner is None else f"NORMAL, hanya melayani chat_id={owner}")


# ----- perakitan -----


def buat_aplikasi(cfg: Config) -> Application:
    builder = (
        Application.builder()
        .token(cfg.telegram_token)
        # Bawaan library hanya menunggu 5 detik; jaringan yang lambat langsung dianggap gagal.
        .connect_timeout(20)
        .read_timeout(30)
        .get_updates_connect_timeout(20)
        .get_updates_read_timeout(30)
        .post_init(saat_terhubung)
    )
    if cfg.telegram_proxy:
        builder = builder.proxy(cfg.telegram_proxy).get_updates_proxy(cfg.telegram_proxy)
    app = builder.build()
    app.bot_data["cfg"] = cfg
    app.add_error_handler(tangani_error)
    # Grup -1 dijalankan sebelum handler lain dan tidak menghalanginya: hanya mencatat.
    app.add_handler(TypeHandler(Update, catat_masuk), group=-1)

    if cfg.owner_chat_id is None:
        log.warning("TELEGRAM_OWNER_CHAT_ID kosong: bot berjalan dalam MODE SETUP (hanya /whoami).")
        app.add_handler(CommandHandler("whoami", cmd_whoami))
        app.add_handler(MessageHandler(filters.ALL, balas_mode_setup))
        return app

    pemilik = filters.Chat(chat_id=cfg.owner_chat_id)
    # Urutan penting: penolak orang lain dipasang PERTAMA supaya tidak ada handler lain yang terpanggil.
    app.add_handler(MessageHandler(~pemilik, abaikan_orang_lain))
    app.add_handler(CommandHandler("start", cmd_start, filters=pemilik))
    app.add_handler(CommandHandler(["bantuan", "help"], cmd_bantuan, filters=pemilik))
    app.add_handler(CommandHandler("status", cmd_status, filters=pemilik))
    app.add_handler(CommandHandler("whoami", cmd_whoami, filters=pemilik))
    app.add_handler(MessageHandler(pemilik & filters.TEXT & ~filters.COMMAND, balas_teks))
    return app
