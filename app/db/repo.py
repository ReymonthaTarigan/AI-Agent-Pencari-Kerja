"""Akses database SQLite. Semua query ke database lewat modul ini."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Buka koneksi. Folder dibuat otomatis bila belum ada."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Buat semua tabel yang belum ada."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


# ===== Pengaturan =====


def get_pengaturan(conn: sqlite3.Connection, kunci: str, default: Any = None) -> Any:
    row = conn.execute("SELECT nilai FROM pengaturan WHERE kunci = ?", (kunci,)).fetchone()
    return json.loads(row["nilai"]) if row else default


def set_pengaturan(conn: sqlite3.Connection, kunci: str, nilai: Any) -> None:
    conn.execute(
        """INSERT INTO pengaturan (kunci, nilai) VALUES (?, ?)
           ON CONFLICT(kunci) DO UPDATE SET nilai = excluded.nilai,
                                            diperbarui_pada = datetime('now')""",
        (kunci, json.dumps(nilai, ensure_ascii=False)),
    )
    conn.commit()


# ===== Log LLM dan kuota =====


def catat_llm(
    conn: sqlite3.Connection,
    *,
    tugas: str,
    penyedia: str,
    model: str,
    sukses: bool,
    versi_prompt: str | None = None,
    token_in: int | None = None,
    token_out: int | None = None,
    latensi_ms: int | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO log_llm (tugas, penyedia, model, versi_prompt, token_in, token_out,
                                latensi_ms, sukses, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tugas, penyedia, model, versi_prompt, token_in, token_out, latensi_ms, int(sukses), error),
    )
    conn.commit()


def hitung_request_24jam(conn: sqlite3.Connection, penyedia: str) -> int:
    """Jumlah request (berhasil maupun gagal) ke penyedia dalam 24 jam terakhir.

    Jendela 24 jam bergulir dipakai karena waktu reset kuota tiap penyedia berbeda;
    cara ini selalu lebih hati-hati daripada reset tengah malam.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM log_llm WHERE penyedia = ? AND waktu >= datetime('now', '-1 day')",
        (penyedia,),
    ).fetchone()
    return int(row["n"])


# ===== Profil =====


def statistik_profil(conn: sqlite3.Connection) -> dict[str, int]:
    def hitung(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return {
        "item": hitung("SELECT COUNT(*) FROM profil_item"),
        "item_lengkap": hitung(
            "SELECT COUNT(*) FROM profil_item WHERE bukti_angka != '[]' OR (link IS NOT NULL AND link != '')"
        ),
        "skill": hitung("SELECT COUNT(*) FROM skill"),
    }
