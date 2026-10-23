"""Alur penilaian lowongan (Tahap 1).

Urutan kerja:
1. [kode] Cek duplikat lewat hash teks. Lowongan yang sama tidak dinilai dua kali, jadi tidak memakai kuota.
2. [LLM]  Ekstrak requirement dari teks lowongan.
3. [LLM]  Nilai setiap requirement terhadap profil, dengan menunjuk id item profil sebagai bukti.
4. [kode] Periksa id bukti, turunkan status yang tidak punya bukti, hitung skor.
5. [kode] Simpan semuanya dalam satu transaksi.

LLM tidak pernah menghitung skor dan tidak pernah menulis ke database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from app.db import repo
from app.llm.client import LLMClient, muat_prompt
from app.llm.schemas import HasilEkstraksi, HasilPencocokan, PenilaianRequirement, Requirement

PROMPT_EKSTRAKSI = "ekstrak_requirement_v3"
PROMPT_PENCOCOKAN = "cocokkan_profil_v1"

# Aturan skor. Ditulis di kode supaya bisa diperiksa, diuji, dan tidak berubah-ubah.
BOBOT = {"wajib": 2.0, "tambahan": 1.0}
NILAI = {"terpenuhi": 1.0, "sebagian": 0.5, "tidak": 0.0}
AMBANG_LAYAK = 70
AMBANG_PERTIMBANGKAN = 50
PANJANG_MAKS_TEKS = 15_000


class ProfilKosong(Exception):
    """Belum ada item profil di database."""


@dataclass
class ItemPenilaian:
    no: int
    teks: str
    kategori: str
    status: str
    bukti: list[str]
    alasan: str


@dataclass
class HasilPenilaian:
    lowongan_id: int
    posisi: str
    perusahaan: str | None
    lokasi: str | None
    tipe_kerja: str | None
    tenggat: str | None
    skor: int
    item: list[ItemPenilaian] = field(default_factory=list)
    duplikat: bool = False

    @property
    def keputusan(self) -> str:
        return keputusan(self.skor)


# ===== Aturan murni (tanpa LLM, tanpa database) =====


def hash_teks(teks: str) -> str:
    """Hash dari teks yang dirapikan, supaya beda spasi atau huruf besar tidak dianggap lowongan baru."""
    return hashlib.sha256(" ".join(teks.lower().split()).encode("utf-8")).hexdigest()


def hitung_skor(item: list[tuple[str, str]]) -> int:
    """Skor 0-100 dari pasangan (kategori, status). Requirement wajib berbobot dua kali requirement tambahan."""
    total = sum(BOBOT[kategori] for kategori, _ in item)
    if not total:
        return 0
    dapat = sum(BOBOT[kategori] * NILAI[status] for kategori, status in item)
    return round(100 * dapat / total)


def keputusan(skor: int) -> str:
    if skor >= AMBANG_LAYAK:
        return "layak dilamar"
    if skor >= AMBANG_PERTIMBANGKAN:
        return "pertimbangkan"
    return "belum cocok"


def durasi_bulan(mulai: str | None, selesai: str | None, hari_ini: date) -> int | None:
    """Jumlah bulan (inklusif) dari YYYY-MM sampai YYYY-MM, atau sampai bulan ini bila masih berjalan."""
    if not mulai:
        return None
    t1, b1 = map(int, mulai.split("-"))
    t2, b2 = map(int, selesai.split("-")) if selesai else (hari_ini.year, hari_ini.month)
    return max(0, (t2 - t1) * 12 + (b2 - b1) + 1)


def periksa_pencocokan(hasil: HasilPencocokan, jumlah: int, id_valid: set[str]) -> list[str]:
    """Masalah isi yang tidak bisa ditangkap schema: nomor hilang/ganda dan id bukti yang tidak ada di profil."""
    masalah = []
    nomor = [p.no for p in hasil.penilaian]
    harus = set(range(1, jumlah + 1))
    if hilang := sorted(harus - set(nomor)):
        masalah.append(f"nomor requirement belum dinilai: {hilang}")
    if asing := sorted(set(nomor) - harus):
        masalah.append(f"nomor requirement tidak ada: {asing}")
    if ganda := sorted({n for n in nomor if nomor.count(n) > 1}):
        masalah.append(f"nomor requirement dinilai lebih dari sekali: {ganda}")
    for p in hasil.penilaian:
        if tak_dikenal := [b for b in p.bukti if b not in id_valid]:
            masalah.append(f"no {p.no}: id bukti tidak ada di profil: {tak_dikenal}")
    return masalah


def tegakkan_bukti(penilaian: list[PenilaianRequirement]) -> list[PenilaianRequirement]:
    """Tidak ada klaim tanpa bukti: status terpenuhi/sebagian tanpa id bukti diturunkan menjadi tidak."""
    hasil = []
    for p in penilaian:
        if p.status != "tidak" and not p.bukti:
            p = p.model_copy(update={"status": "tidak", "alasan": f"{p.alasan} (diturunkan: tanpa bukti)".strip()})
        elif p.status == "tidak" and p.bukti:
            p = p.model_copy(update={"bukti": []})
        hasil.append(p)
    return sorted(hasil, key=lambda p: p.no)


# ===== Langkah yang memakai LLM =====


def ekstrak(client: LLMClient, teks: str, hari_ini: date) -> HasilEkstraksi:
    prompt = muat_prompt(f"{PROMPT_EKSTRAKSI}.md", lowongan=teks[:PANJANG_MAKS_TEKS], hari_ini=hari_ini.isoformat())
    return client.panggil_json(
        "ekstrak_requirement", [{"role": "user", "content": prompt}], HasilEkstraksi, versi_prompt=PROMPT_EKSTRAKSI
    )


def profil_untuk_prompt(conn: sqlite3.Connection, hari_ini: date) -> tuple[str, set[str]]:
    """Profil dalam bentuk JSON untuk LLM. Identitas (nama, email, telepon) sengaja tidak dikirim."""
    item = [
        {
            "id": i["id"],
            "jenis": i["jenis"],
            "judul": i["judul"],
            "organisasi": i["organisasi"],
            "periode": f"{i['mulai'] or '?'} s/d {i['selesai'] or 'sekarang'}",
            "durasi_bulan": durasi_bulan(i["mulai"], i["selesai"], hari_ini),
            "deskripsi": i["deskripsi"],
            "bukti_angka": i["bukti_angka"],
            "skill": i["skill"],
        }
        for i in repo.ambil_profil(conn)
    ]
    data = {"item": item, "skill": repo.ambil_skill(conn)}
    return json.dumps(data, ensure_ascii=False, indent=1), {i["id"] for i in item}


def cocokkan(
    client: LLMClient, conn: sqlite3.Connection, requirement: list[Requirement], hari_ini: date
) -> list[PenilaianRequirement]:
    profil_json, id_valid = profil_untuk_prompt(conn, hari_ini)
    if not id_valid:
        raise ProfilKosong("Profil masih kosong. Jalankan: python -m scripts.init_db")
    daftar = [
        {"no": n, "teks": r.teks_asli, "kategori": r.kategori, "jenis": r.jenis, "skill_alternatif": r.skill}
        for n, r in enumerate(requirement, start=1)
    ]
    prompt = muat_prompt(
        f"{PROMPT_PENCOCOKAN}.md", profil=profil_json, requirement=json.dumps(daftar, ensure_ascii=False, indent=1)
    )
    hasil = client.panggil_json(
        "cocokkan_profil",
        [{"role": "user", "content": prompt}],
        HasilPencocokan,
        versi_prompt=PROMPT_PENCOCOKAN,
        validasi=lambda h: periksa_pencocokan(h, len(requirement), id_valid),
    )
    return tegakkan_bukti(hasil.penilaian)


# ===== Alur lengkap =====


def nilai_lowongan(
    client: LLMClient,
    conn: sqlite3.Connection,
    teks: str,
    *,
    sumber: str = "manual",
    url: str | None = None,
    hari_ini: date | None = None,
) -> HasilPenilaian:
    hari_ini = hari_ini or date.today()
    kunci = hash_teks(teks)
    if (lowongan_id := repo.cari_lowongan_hash(conn, kunci)) is not None:
        return muat_hasil(conn, lowongan_id, duplikat=True)

    ekstraksi = ekstrak(client, teks, hari_ini)
    penilaian = cocokkan(client, conn, ekstraksi.requirement, hari_ini)
    kategori = {n: r.kategori for n, r in enumerate(ekstraksi.requirement, start=1)}
    skor = hitung_skor([(kategori[p.no], p.status) for p in penilaian])

    lowongan_id = repo.simpan_penilaian(
        conn,
        lowongan={
            "sumber": sumber,
            "url": url,
            "perusahaan": ekstraksi.perusahaan,
            "posisi": ekstraksi.posisi,
            "lokasi": ekstraksi.lokasi,
            "tipe_kerja": ekstraksi.tipe_kerja,
            "tenggat": ekstraksi.tenggat.isoformat() if ekstraksi.tenggat else None,
            "deskripsi": teks,
            "hash_deskripsi": kunci,
        },
        requirement=[{"no": n, **r.model_dump()} for n, r in enumerate(ekstraksi.requirement, start=1)],
        penilaian=[p.model_dump() for p in penilaian],
        skor=skor,
    )
    return muat_hasil(conn, lowongan_id)


def muat_hasil(conn: sqlite3.Connection, lowongan_id: int, duplikat: bool = False) -> HasilPenilaian:
    data = repo.ambil_penilaian(conn, lowongan_id)
    lo = data["lowongan"]
    return HasilPenilaian(
        lowongan_id=lowongan_id,
        posisi=lo["posisi"],
        perusahaan=lo["perusahaan"],
        lokasi=lo["lokasi"],
        tipe_kerja=lo["tipe_kerja"],
        tenggat=lo["tenggat"],
        skor=round(lo["skor_kecocokan"] or 0),
        item=[
            ItemPenilaian(i["no"], i["teks_asli"], i["kategori"], i["status"], i["bukti"], i["alasan"] or "")
            for i in data["item"]
        ],
        duplikat=duplikat,
    )


# ===== Tampilan =====

_BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
_TANDA = {"terpenuhi": "✓", "sebagian": "~", "tidak": "✗"}
_JUDUL = {"terpenuhi": "Terpenuhi", "sebagian": "Sebagian", "tidak": "Belum ada bukti"}


def _tanggal(iso: str) -> str:
    t, b, h = iso.split("-")
    return f"{int(h)} {_BULAN[int(b) - 1]} {t}"


def _pendek(teks: str, maks: int = 90) -> str:
    teks = " ".join(teks.split())
    return teks if len(teks) <= maks else teks[: maks - 1] + "…"


def format_pesan(hasil: HasilPenilaian) -> str:
    """Ringkasan untuk Telegram (teks biasa, tanpa Markdown, di bawah batas 4.096 karakter)."""
    baris = []
    if hasil.duplikat:
        baris += [f"Lowongan ini sudah pernah dinilai (#{hasil.lowongan_id}). Hasil sebelumnya:", ""]
    baris.append(hasil.posisi)
    keterangan = " · ".join(x for x in (hasil.perusahaan, hasil.lokasi, hasil.tipe_kerja) if x)
    if keterangan:
        baris.append(keterangan)
    if hasil.tenggat:
        baris.append(f"Tenggat: {_tanggal(hasil.tenggat)}")
    baris += ["", f"Skor {hasil.skor}/100: {hasil.keputusan.upper()}"]
    for status in ("terpenuhi", "sebagian", "tidak"):
        kelompok = [i for i in hasil.item if i.status == status]
        if not kelompok:
            continue
        baris += ["", f"{_JUDUL[status]} ({len(kelompok)})"]
        for i in kelompok:
            bukti = f" · {', '.join(i.bukti)}" if i.bukti else ""
            baris.append(f"{_TANDA[status]} [{i.kategori}] {_pendek(i.teks)}{bukti}")
            if status == "sebagian" and i.alasan:
                baris.append(f"   {_pendek(i.alasan, 120)}")
    teks = "\n".join(baris)
    return teks if len(teks) <= 4000 else teks[:3990] + "\n…"
