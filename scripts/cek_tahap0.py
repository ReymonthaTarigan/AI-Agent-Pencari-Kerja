"""Periksa apakah gerbang Tahap 0 sudah terpenuhi.

    python -m scripts.cek_tahap0             # termasuk 1 request uji ke setiap penyedia LLM
    python -m scripts.cek_tahap0 --tanpa-llm # hemat kuota, lewati uji LLM

Gerbang Tahap 0:
1. Bot Telegram hanya membalas pemiliknya (TELEGRAM_OWNER_CHAT_ID terisi dan /start sudah dikirim).
2. LLM bisa mengembalikan JSON yang valid.
3. Google API bisa membaca dan menulis.
4. Profil terisi minimal 10 item yang lengkap dengan bukti.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import httpx

from app.config import Config, get_config
from app.db import profil, repo
from app.integrasi.google_api import PerluLoginUlang, muat_kredensial
from app.llm.client import LLMClient, OutputTidakValid, SemuaPenyediaGagal
from app.llm.schemas import HasilEkstraksi

hasil_cek: list[tuple[str, str, str]] = []


def catat(status: str, nama: str, keterangan: str = "") -> None:
    hasil_cek.append((status, nama, keterangan))
    print(f"[{status}] {nama}" + (f": {keterangan}" if keterangan else ""))


def cek_env(cfg: Config) -> None:
    catat("OK" if cfg.telegram_token else "GAGAL", "TELEGRAM_BOT_TOKEN", "" if cfg.telegram_token else "belum diisi")
    catat(
        "OK" if cfg.owner_chat_id else "GAGAL",
        "TELEGRAM_OWNER_CHAT_ID",
        "" if cfg.owner_chat_id else "jalankan bot, kirim /whoami, isi hasilnya",
    )
    catat(
        "OK" if cfg.token_key else "GAGAL",
        "TOKEN_KEY",
        "" if cfg.token_key else "jalankan python -m scripts.buat_kunci",
    )
    if not cfg.penyedia:
        catat("GAGAL", "Model LLM", "isi LLM_UTAMA_MODEL di .env")
    for p in cfg.penyedia:
        kurang = LLMClient.kunci_kurang(p)
        if kurang:
            catat("GAGAL", f"Kunci API {p.nama}", f"belum diisi salah satu dari: {', '.join(kurang)}")
        else:
            catat("OK", f"Kunci API {p.nama}", "model lokal" if p.model.startswith("ollama/") else "")
    catat(
        "OK" if cfg.google_client_secret.exists() else "GAGAL",
        "File OAuth Google",
        "" if cfg.google_client_secret.exists() else f"tidak ada di {cfg.google_client_secret}",
    )


def cek_profil(cfg: Config, conn: sqlite3.Connection) -> None:
    if not cfg.profil_path.exists():
        catat("GAGAL", "Profil", f"file tidak ditemukan: {cfg.profil_path}")
        return
    h = profil.validasi_profil(profil.baca_file(cfg.profil_path))
    if not h.valid:
        catat("GAGAL", "Profil valid", f"{len(h.error)} kesalahan, jalankan python -m scripts.init_db untuk detail")
        return
    n = h.jumlah_item_lengkap
    catat("OK" if n >= 10 else "GAGAL", "Item profil lengkap dengan bukti", f"{n} dari minimal 10")
    s = repo.statistik_profil(conn)
    if s["item"] != len(h.profil.item):
        catat("GAGAL", "Profil di database", "belum sama dengan file, jalankan python -m scripts.init_db")
    else:
        catat("OK", "Profil di database", f"{s['item']} item, {s['skill']} skill")
    if "data_dummy" in str(cfg.profil_path):
        catat("PERINGATAN", "Profil", "masih memakai data dummy")


def cek_telegram(cfg: Config, conn: sqlite3.Connection) -> None:
    if not cfg.telegram_token:
        return
    try:
        r = httpx.get(f"https://api.telegram.org/bot{cfg.telegram_token}/getMe", timeout=15)
        data = r.json()
        if data.get("ok"):
            catat("OK", "Token bot Telegram", f"@{data['result']['username']}")
        else:
            catat("GAGAL", "Token bot Telegram", data.get("description", "ditolak Telegram"))
    except httpx.HTTPError as e:
        catat("GAGAL", "Token bot Telegram", f"tidak bisa menghubungi Telegram ({type(e).__name__})")
    if repo.get_pengaturan(conn, "owner_terverifikasi_pada"):
        catat("OK", "Bot membalas pemilik", "/start sudah diterima dari chat_id pemilik")
    else:
        catat("GAGAL", "Bot membalas pemilik", "jalankan python -m app.main lalu kirim /start ke bot")


def cek_llm(cfg: Config, conn: sqlite3.Connection) -> None:
    for p in cfg.penyedia:
        client = LLMClient(cfg, conn, hanya=[p.nama])
        pesan = [
            {
                "role": "user",
                "content": (
                    "Balas HANYA dengan JSON: "
                    '{"posisi": "Data Analyst", "perusahaan": null, "requirement": '
                    '[{"teks_asli": "Menguasai SQL", "kategori": "wajib", '
                    '"skill_normal": "sql", "tahun_pengalaman": null}]}'
                ),
            }
        ]
        try:
            client.panggil_json("cek_tahap0", pesan, HasilEkstraksi, versi_prompt="cek_tahap0")
            catat("OK", f"LLM {p.nama} mengembalikan JSON valid", p.model)
        except (SemuaPenyediaGagal, OutputTidakValid) as e:
            catat("GAGAL", f"LLM {p.nama}", str(e)[:200])


def cek_google(cfg: Config, conn: sqlite3.Connection) -> None:
    try:
        muat_kredensial(cfg, interaktif=False)
        catat("OK", "Login Google", "token tersimpan dan masih berlaku")
    except PerluLoginUlang as e:
        catat("GAGAL", "Login Google", str(e))
        return
    for kunci, nama in (
        ("calendar_id", "Kalender 'Cari Kerja'"),
        ("tracker_spreadsheet_id", "Spreadsheet tracker"),
        ("drive_folder_id", "Folder Drive"),
    ):
        ada = repo.get_pengaturan(conn, kunci)
        catat("OK" if ada else "GAGAL", nama, "" if ada else "jalankan python -m scripts.uji_koneksi_google")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tanpa-llm", action="store_true")
    args = parser.parse_args()

    cfg = get_config()
    conn = repo.connect(cfg.db_path)
    repo.init_db(conn)

    print("== Konfigurasi .env ==")
    cek_env(cfg)
    print("\n== Profil ==")
    cek_profil(cfg, conn)
    print("\n== Telegram ==")
    cek_telegram(cfg, conn)
    print("\n== LLM ==")
    if args.tanpa_llm:
        catat("PERINGATAN", "Uji LLM", "dilewati (--tanpa-llm)")
    else:
        cek_llm(cfg, conn)
    print("\n== Google ==")
    cek_google(cfg, conn)
    conn.close()

    gagal = [h for h in hasil_cek if h[0] == "GAGAL"]
    print("\n" + "=" * 40)
    if gagal:
        print(f"Tahap 0 BELUM selesai: {len(gagal)} pemeriksaan gagal.")
        return 1
    print("Gerbang Tahap 0 terpenuhi. Siap lanjut ke Tahap 1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
