"""Buat database (bila belum ada), validasi profil, lalu muat profil ke database.

Jalankan dari folder proyek:
    python -m scripts.init_db
Aman dijalankan berulang kali. Setiap kali profil diubah, jalankan lagi.
"""

from __future__ import annotations

import sys

from app.config import get_config
from app.db import profil, repo


def main() -> int:
    cfg = get_config()
    print(f"Database : {cfg.db_path}")
    print(f"Profil   : {cfg.profil_path}")

    if not cfg.profil_path.exists():
        print(f"[GAGAL] File profil tidak ditemukan: {cfg.profil_path}")
        return 1

    hasil = profil.validasi_profil(profil.baca_file(cfg.profil_path))
    for p in hasil.peringatan:
        print(f"[PERINGATAN] {p}")
    if not hasil.valid:
        for e in hasil.error:
            print(f"[GAGAL] {e}")
        print("Profil tidak dimuat. Perbaiki kesalahan di atas lalu jalankan lagi.")
        return 1

    conn = repo.connect(cfg.db_path)
    repo.init_db(conn)
    profil.muat_ke_db(conn, hasil.profil)
    s = repo.statistik_profil(conn)
    conn.close()

    print(f"[OK] {s['item']} item profil ({s['item_lengkap']} lengkap dengan bukti) dan {s['skill']} skill dimuat.")
    if s["item_lengkap"] < 10:
        print("[PERINGATAN] Gerbang Tahap 0 butuh minimal 10 item lengkap dengan bukti.")
    if "data_dummy" in str(cfg.profil_path):
        print("[INFO] Masih memakai profil DUMMY. Ganti PROFIL_PATH di .env setelah profil asli siap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
