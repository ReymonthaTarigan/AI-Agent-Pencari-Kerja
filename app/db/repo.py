"""Akses database SQLite. Semua query ke database lewat modul ini."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
VERSI_SKEMA = 2


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Buka koneksi. Folder dibuat otomatis bila belum ada."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Buat semua tabel yang belum ada, setelah memigrasikan database versi lama bila perlu."""
    versi = conn.execute("PRAGMA user_version").fetchone()[0]
    if versi < 2:
        _migrasi_ke_2(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version = {VERSI_SKEMA}")
    conn.commit()


def _migrasi_ke_2(conn: sqlite3.Connection) -> None:
    """Tahap 1 mengganti kolom skill_normal (satu skill) menjadi skill (daftar alternatif).

    Di Tahap 0 tabel requirement dan kecocokan belum pernah diisi, jadi aman dibuat ulang. Bila ternyata
    sudah berisi, migrasi berhenti supaya tidak ada data yang hilang diam-diam.
    """
    kolom = [r[1] for r in conn.execute("PRAGMA table_info(requirement)")]
    if "skill_normal" not in kolom:
        return
    terisi = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("requirement", "kecocokan"))
    if terisi:
        raise RuntimeError("Migrasi database dibatalkan: tabel requirement/kecocokan versi lama sudah berisi data.")
    conn.executescript("DROP TABLE IF EXISTS kecocokan; DROP TABLE IF EXISTS requirement;")


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


def ambil_profil(conn: sqlite3.Connection) -> list[dict]:
    """Semua item profil beserta nama skill yang dibuktikannya."""
    skill_per_item: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT pis.item_id, s.nama_normal FROM profil_item_skill pis JOIN skill s ON s.id = pis.skill_id ORDER BY s.id"
    ):
        skill_per_item.setdefault(r["item_id"], []).append(r["nama_normal"])
    return [
        {**dict(r), "bukti_angka": json.loads(r["bukti_angka"]), "skill": skill_per_item.get(r["id"], [])}
        for r in conn.execute("SELECT * FROM profil_item ORDER BY id")
    ]


def ambil_skill(conn: sqlite3.Connection) -> list[dict]:
    return [
        {"nama": r["nama_normal"], "level": r["level"], "alias": json.loads(r["alias"])}
        for r in conn.execute("SELECT * FROM skill ORDER BY id")
    ]


# ===== Lowongan dan penilaian =====


def cari_lowongan_hash(conn: sqlite3.Connection, hash_deskripsi: str) -> int | None:
    row = conn.execute("SELECT id FROM lowongan WHERE hash_deskripsi = ?", (hash_deskripsi,)).fetchone()
    return int(row["id"]) if row else None


def simpan_penilaian(
    conn: sqlite3.Connection,
    *,
    lowongan: dict,
    requirement: list[dict],
    penilaian: list[dict],
    skor: float,
) -> int:
    """Simpan lowongan, requirement, kecocokan, dan lamaran dalam SATU transaksi; kembalikan id lowongan."""
    with conn:
        lowongan_id = conn.execute(
            """INSERT INTO lowongan (sumber, url, perusahaan, posisi, lokasi, tipe_kerja, tenggat,
                                     deskripsi, hash_deskripsi)
               VALUES (:sumber, :url, :perusahaan, :posisi, :lokasi, :tipe_kerja, :tenggat,
                       :deskripsi, :hash_deskripsi)""",
            lowongan,
        ).lastrowid
        id_requirement = {}
        for r in requirement:
            id_requirement[r["no"]] = conn.execute(
                """INSERT INTO requirement (lowongan_id, no, teks_asli, kategori, jenis, skill, tahun_pengalaman)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    lowongan_id,
                    r["no"],
                    r["teks_asli"],
                    r["kategori"],
                    r["jenis"],
                    json.dumps(r["skill"], ensure_ascii=False),
                    r["tahun_pengalaman"],
                ),
            ).lastrowid
        for p in penilaian:
            conn.execute(
                """INSERT INTO kecocokan (lowongan_id, requirement_id, status, bukti_item_ids, alasan)
                   VALUES (?, ?, ?, ?, ?)""",
                (lowongan_id, id_requirement[p["no"]], p["status"], json.dumps(p["bukti"]), p["alasan"]),
            )
        conn.execute(
            "INSERT INTO lamaran (lowongan_id, status, skor_kecocokan) VALUES (?, 'ditemukan', ?)",
            (lowongan_id, skor),
        )
    return int(lowongan_id)


def ambil_penilaian(conn: sqlite3.Connection, lowongan_id: int) -> dict:
    """Lowongan, skor, dan penilaian setiap requirement (urut sesuai nomor)."""
    lowongan = conn.execute(
        """SELECT lo.*, la.skor_kecocokan, la.status AS status_lamaran
           FROM lowongan lo JOIN lamaran la ON la.lowongan_id = lo.id WHERE lo.id = ?""",
        (lowongan_id,),
    ).fetchone()
    item = conn.execute(
        """SELECT r.no, r.teks_asli, r.kategori, r.jenis, r.skill, k.status, k.bukti_item_ids, k.alasan
           FROM requirement r JOIN kecocokan k ON k.requirement_id = r.id
           WHERE r.lowongan_id = ? ORDER BY r.no""",
        (lowongan_id,),
    ).fetchall()
    return {
        "lowongan": dict(lowongan),
        "item": [{**dict(i), "skill": json.loads(i["skill"]), "bukti": json.loads(i["bukti_item_ids"])} for i in item],
    }


def daftar_lamaran(conn: sqlite3.Connection, *, urut: str = "terbaru", batas: int | None = None) -> list[dict]:
    """Ringkasan lamaran untuk /daftar dan tracker Sheets."""
    urutan = {"terbaru": "la.dibuat_pada DESC, la.id DESC", "skor": "la.skor_kecocokan DESC, la.id DESC"}[urut]
    sql = f"""SELECT la.id, lo.id AS lowongan_id, lo.perusahaan, lo.posisi, la.skor_kecocokan, la.status,
                     lo.tenggat, lo.url, la.folder_drive_url, la.diperbarui_pada
              FROM lamaran la JOIN lowongan lo ON lo.id = la.lowongan_id
              ORDER BY {urutan}"""
    if batas:
        sql += f" LIMIT {int(batas)}"
    return [dict(r) for r in conn.execute(sql)]
