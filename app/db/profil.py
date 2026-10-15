"""Validasi dan pemuatan profil dari file JSON ke database.

Profil adalah satu-satunya sumber kebenaran tentang diri pengguna. Aturan yang
ditegakkan di sini:
- id unik, format P-xx untuk item dan S-xx untuk skill;
- setiap skill harus didukung minimal satu item profil (tidak ada skill tanpa bukti);
- skill yang dirujuk sebuah item harus ada di daftar skill, dan tidak boleh ditulis dua kali;
- tanggal mulai tidak boleh setelah tanggal selesai.
Item tanpa bukti_angka dan tanpa link tetap dimuat, tetapi diberi peringatan dan
tidak dihitung sebagai item lengkap.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

JenisItem = Literal[
    "pendidikan",
    "pengalaman_kerja",
    "magang",
    "proyek_magang",
    "proyek_kuliah",
    "proyek_pribadi",
    "organisasi",
    "sertifikat",
    "pencapaian",
]


class Identitas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nama: str
    email: str
    telepon: str | None = None
    kota: str | None = None
    linkedin: str | None = None
    github: str | None = None
    portofolio: str | None = None


class SkillMasuk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^S-\d{2,}$")
    nama: str = Field(min_length=1)
    alias: list[str] = []
    level: Literal["dasar", "menengah", "mahir"]


class ItemMasuk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^P-\d{2,}$")
    jenis: JenisItem
    judul: str = Field(min_length=3)
    organisasi: str | None = None
    mulai: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    selesai: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    deskripsi: str = Field(min_length=20)
    bukti_angka: list[str] = []
    link: str | None = None
    skill: list[str] = []

    @property
    def lengkap(self) -> bool:
        return bool(self.bukti_angka) or bool(self.link)


class ProfilMasuk(BaseModel):
    model_config = ConfigDict(extra="ignore")  # kunci seperti "_petunjuk" diabaikan
    identitas: Identitas
    skill: list[SkillMasuk]
    item: list[ItemMasuk]


@dataclass
class HasilValidasi:
    profil: ProfilMasuk | None = None
    error: list[str] = field(default_factory=list)
    peringatan: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.profil is not None and not self.error

    @property
    def jumlah_item_lengkap(self) -> int:
        return sum(1 for i in self.profil.item if i.lengkap) if self.profil else 0


def validasi_profil(data: dict) -> HasilValidasi:
    hasil = HasilValidasi()
    try:
        profil = ProfilMasuk.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            lokasi = ".".join(str(x) for x in err["loc"])
            hasil.error.append(f"{lokasi}: {err['msg']}")
        return hasil
    hasil.profil = profil

    id_item = [i.id for i in profil.item]
    id_skill = [s.id for s in profil.skill]
    for nama, daftar in (("item", id_item), ("skill", id_skill)):
        duplikat = sorted({x for x in daftar if daftar.count(x) > 1})
        if duplikat:
            hasil.error.append(f"id {nama} duplikat: {', '.join(duplikat)}")
    nama_skill = [s.nama.strip().lower() for s in profil.skill]
    duplikat_nama = sorted({x for x in nama_skill if nama_skill.count(x) > 1})
    if duplikat_nama:
        hasil.error.append(f"nama skill duplikat: {', '.join(duplikat_nama)}")

    skill_dipakai: set[str] = set()
    for item in profil.item:
        ganda = sorted({sid for sid in item.skill if item.skill.count(sid) > 1})
        if ganda:
            hasil.error.append(f"{item.id}: skill ditulis lebih dari sekali: {', '.join(ganda)}")
        for sid in item.skill:
            if sid not in id_skill:
                hasil.error.append(f"{item.id}: skill {sid} tidak ada di daftar skill")
            skill_dipakai.add(sid)
        if item.mulai and item.selesai and item.mulai > item.selesai:
            hasil.error.append(f"{item.id}: mulai ({item.mulai}) setelah selesai ({item.selesai})")
        if not item.lengkap:
            hasil.peringatan.append(
                f"{item.id} ({item.judul}): belum ada bukti_angka atau link, "
                "sehingga tidak bisa dipakai sebagai bukti di CV"
            )

    for s in profil.skill:
        if s.id not in skill_dipakai:
            hasil.error.append(
                f"{s.id} ({s.nama}): tidak didukung item mana pun. "
                "Tambahkan id skill ini ke kolom skill pada item yang membuktikannya, atau hapus skill-nya."
            )
    return hasil


def baca_file(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def muat_ke_db(conn: sqlite3.Connection, profil: ProfilMasuk) -> None:
    """Ganti seluruh isi tabel profil dengan isi file, dalam satu transaksi."""
    with conn:
        conn.execute("DELETE FROM profil_item_skill")
        conn.execute("DELETE FROM profil_item")
        conn.execute("DELETE FROM skill")
        for s in profil.skill:
            conn.execute(
                "INSERT INTO skill (id, nama_normal, alias, level) VALUES (?, ?, ?, ?)",
                (s.id, s.nama.strip().lower(), json.dumps(s.alias, ensure_ascii=False), s.level),
            )
        for i in profil.item:
            conn.execute(
                """INSERT INTO profil_item (id, jenis, judul, organisasi, mulai, selesai,
                                            deskripsi, bukti_angka, link)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    i.id,
                    i.jenis,
                    i.judul,
                    i.organisasi,
                    i.mulai,
                    i.selesai,
                    i.deskripsi,
                    json.dumps(i.bukti_angka, ensure_ascii=False),
                    i.link,
                ),
            )
            for sid in i.skill:
                conn.execute("INSERT INTO profil_item_skill (item_id, skill_id) VALUES (?, ?)", (i.id, sid))
        conn.execute(
            """INSERT INTO pengaturan (kunci, nilai) VALUES ('identitas', ?)
               ON CONFLICT(kunci) DO UPDATE SET nilai = excluded.nilai,
                                                diperbarui_pada = datetime('now')""",
            (profil.identitas.model_dump_json(),),
        )
