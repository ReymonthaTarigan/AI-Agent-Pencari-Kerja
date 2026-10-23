"""Sinkronisasi tracker lamaran ke Google Sheets.

Database adalah sumber kebenaran; tab "Lamaran" di Sheets hanya tampilan dan selalu ditulis ulang utuh dari
database, jadi aman dijalankan berkali-kali dan tidak pernah membuat baris ganda.
"""

from __future__ import annotations

import logging
import sqlite3

from app.config import Config
from app.db import repo
from app.integrasi.google_api import Google, PerluLoginUlang, muat_kredensial

log = logging.getLogger(__name__)


def baris_tracker(conn: sqlite3.Connection) -> list[list]:
    """Satu baris per lamaran, urut skor tertinggi, sesuai kolom KOLOM_LAMARAN."""
    return [
        [
            r["perusahaan"] or "",
            r["posisi"],
            round(r["skor_kecocokan"] or 0),
            r["status"],
            r["tenggat"] or "",
            r["url"] or "",
            r["folder_drive_url"] or "",
            (r["diperbarui_pada"] or "")[:10],
        ]
        for r in repo.daftar_lamaran(conn, urut="skor")
    ]


def sinkron_tracker(cfg: Config, conn: sqlite3.Connection) -> str | None:
    """Tulis ulang tab Lamaran. Mengembalikan pesan untuk pengguna bila gagal, None bila berhasil."""
    try:
        creds = muat_kredensial(cfg, interaktif=False)
    except PerluLoginUlang as e:
        return f"Tracker Sheets tidak diperbarui. {e}"
    try:
        google = Google(cfg, creds)
        google.tulis_tracker(google.pastikan_tracker(conn), baris_tracker(conn))
    except Exception as e:  # kegagalan Sheets tidak boleh menggagalkan penilaian yang sudah tersimpan
        log.warning("Sinkron tracker gagal: %s", e)
        return f"Tracker Sheets tidak diperbarui ({type(e).__name__}). Data tetap tersimpan di database."
    return None
