"""Uji koneksi Google: baca email, buat kalender + event, buat folder Drive, buat tracker Sheets.

    python -m scripts.uji_koneksi_google --login   # pertama kali, atau saat token kedaluwarsa (membuka browser)
    python -m scripts.uji_koneksi_google           # uji ulang tanpa login
Event uji dibuat besok pukul 09.00 di kalender "Cari Kerja" dan boleh dihapus.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from app.config import get_config
from app.db import repo
from app.integrasi.google_api import NAMA_KALENDER, Google, PerluLoginUlang, muat_kredensial


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="buka browser untuk login Google")
    args = parser.parse_args()

    cfg = get_config()
    conn = repo.connect(cfg.db_path)
    repo.init_db(conn)
    try:
        creds = muat_kredensial(cfg, interaktif=args.login)
    except (PerluLoginUlang, FileNotFoundError) as e:
        print(f"[GAGAL] {e}")
        return 1
    g = Google(cfg, creds)

    print("1. Gmail: 5 email terbaru")
    for e in g.email_terbaru(5):
        print(f"   - {e['tanggal'][:16]} | {e['dari'][:40]} | {e['subjek'][:60]}")

    print("2. Calendar")
    cal_id = g.pastikan_kalender(conn)
    besok = datetime.now(g.tz).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ev = g.buat_event(cal_id, "Uji agent (boleh dihapus)", besok, 15, "Event uji koneksi Tahap 0.")
    print(f"   [OK] Kalender '{NAMA_KALENDER}' siap, event uji dibuat: {ev.get('htmlLink')}")

    print("3. Drive")
    folder_id = g.pastikan_folder_induk(conn)
    print(f"   [OK] Folder: https://drive.google.com/drive/folders/{folder_id}")

    print("4. Sheets")
    sheet_id = g.pastikan_tracker(conn)
    g.tambah_log_sheet(sheet_id, "Uji koneksi Tahap 0 berhasil")
    print(f"   [OK] Tracker: https://docs.google.com/spreadsheets/d/{sheet_id}")

    repo.set_pengaturan(conn, "google_teruji_pada", datetime.now(g.tz).isoformat())
    conn.close()
    print("\nSemua uji Google berhasil.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
