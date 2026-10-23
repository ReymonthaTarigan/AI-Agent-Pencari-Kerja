"""Bot Telegram: pusat kendali agent.

Aturan akses (ditegakkan kode, bukan prompt):
- Selama TELEGRAM_OWNER_CHAT_ID kosong, bot dalam MODE SETUP: hanya /whoami yang dibalas,
  supaya kamu bisa mengetahui chat_id milikmu.
- Setelah TELEGRAM_OWNER_CHAT_ID diisi, bot HANYA melayani chat_id itu. Pesan dari orang lain
  tidak dibalas sama sekali, hanya dicatat di log.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

from app.config import Config
from app.db import repo
from app.llm.client import LLMClient, OutputTidakValid, SemuaPenyediaGagal
from app.workflows.penilaian import ProfilKosong, format_pesan, nilai_lowongan
from app.workflows.tracker import sinkron_tracker

log = logging.getLogger(__name__)

BANTUAN = (
    "Cara pakai:\n"
    "- Tempel teks lowongan lengkap ke chat ini. Bot akan menilai kecocokannya dengan profilmu.\n\n"
    "Perintah:\n"
    "/daftar - 10 lowongan terakhir yang dinilai\n"
    "/status - ringkasan profil dan pemakaian kuota LLM\n"
    "/bantuan - daftar perintah"
)
PANJANG_MIN_LOWONGAN = 200  # pesan lebih pendek dari ini dianggap bukan teks lowongan


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


def _proses_lowongan(cfg: Config, teks: str) -> str:
    """Dijalankan di thread terpisah karena memanggil LLM dan Google secara blocking."""
    conn = repo.connect(cfg.db_path)
    try:
        repo.init_db(conn)
        hasil = nilai_lowongan(LLMClient(cfg, conn), conn, teks, sumber="telegram")
        catatan = None if hasil.duplikat else sinkron_tracker(cfg, conn)
    finally:
        conn.close()
    pesan = format_pesan(hasil)
    return f"{pesan}\n\n{catatan}" if catatan else pesan


async def terima_teks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    teks = update.effective_message.text or ""
    if len(teks) < PANJANG_MIN_LOWONGAN:
        await update.effective_message.reply_text(
            "Untuk menilai lowongan, tempel teks lowongan lengkapnya (bukan hanya judul atau link).\n\n" + BANTUAN
        )
        return
    await update.effective_message.reply_text("Menilai lowongan, biasanya 10 sampai 60 detik...")
    # Satu penilaian dalam satu waktu, supaya batas request per menit tetap dipatuhi.
    async with context.application.bot_data["kunci_proses"]:
        try:
            pesan = await asyncio.to_thread(_proses_lowongan, _cfg(context), teks)
        except ProfilKosong as e:
            pesan = str(e)
        except SemuaPenyediaGagal as e:
            pesan = f"Semua penyedia LLM sedang tidak bisa dipakai. Coba lagi nanti.\n\nDetail: {e}"
        except OutputTidakValid:
            log.exception("Output LLM tidak valid saat menilai lowongan")
            pesan = "Lowongan ini tidak berhasil dibaca dengan benar. Coba kirim ulang, atau rapikan teksnya."
    await update.effective_message.reply_text(pesan)


async def cmd_daftar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    conn = repo.connect(cfg.db_path)
    try:
        repo.init_db(conn)
        daftar = repo.daftar_lamaran(conn, urut="terbaru", batas=10)
    finally:
        conn.close()
    if not daftar:
        await update.effective_message.reply_text("Belum ada lowongan yang dinilai. Tempel teks lowongan ke chat ini.")
        return
    baris = ["10 lowongan terakhir (skor · posisi · perusahaan · status):", ""]
    for r in daftar:
        perusahaan = r["perusahaan"] or "-"
        baris.append(f"{round(r['skor_kecocokan'] or 0):>3} · {r['posisi']} · {perusahaan} · {r['status']}")
    await update.effective_message.reply_text("\n".join(baris))


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
    app.bot_data["kunci_proses"] = asyncio.Lock()
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
    app.add_handler(CommandHandler("daftar", cmd_daftar, filters=pemilik))
    app.add_handler(CommandHandler("whoami", cmd_whoami, filters=pemilik))
    app.add_handler(MessageHandler(pemilik & filters.TEXT & ~filters.COMMAND, terima_teks))
    return app
