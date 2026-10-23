"""Aturan penilaian lowongan dan alur lengkapnya, dengan LLM palsu (tanpa internet)."""

import json
from dataclasses import replace
from datetime import date

import pytest

from app.config import ROOT, PenyediaLLM, muat_config
from app.db import profil, repo
from app.llm import client as llm
from app.llm.schemas import HasilPencocokan, PenilaianRequirement
from app.workflows import penilaian as pn
from app.workflows.tracker import baris_tracker

HARI_INI = date(2026, 10, 15)
TEKS = (ROOT / "data_dummy" / "lowongan_contoh.txt").read_text(encoding="utf-8")

EKSTRAKSI = {
    "posisi": "Junior Data Analyst",
    "perusahaan": "PT Contoh Digital Indonesia",
    "lokasi": "Bandung",
    "tipe_kerja": "Hybrid",
    "tenggat": "2026-11-30",
    "requirement": [
        {"teks_asli": "Menguasai SQL", "kategori": "wajib", "jenis": "skill", "skill": ["SQL"]},
        {
            "teks_asli": "Dashboard dengan Looker Studio, Tableau, atau Power BI",
            "kategori": "Wajib",
            "jenis": "skill",
            "skill": ["looker studio", "tableau", "power bi"],
        },
        {
            "teks_asli": "Pengalaman minimal 1 tahun",
            "kategori": "wajib",
            "jenis": "pengalaman",
            "skill": [],
            "tahun_pengalaman": 1,
        },
        {"teks_asli": "Pernah menggunakan BigQuery", "kategori": "tambahan", "jenis": "skill", "skill": ["bigquery"]},
    ],
}
PENCOCOKAN = {
    "penilaian": [
        {"no": 1, "status": "terpenuhi", "bukti": ["P-03"], "alasan": "Dashboard terhubung ke SQL."},
        {"no": 2, "status": "terpenuhi", "bukti": ["P-03"], "alasan": "Membuat dashboard Looker Studio."},
        {"no": 3, "status": "sebagian", "bukti": ["P-02"], "alasan": "Magang baru 4 bulan."},
        {"no": 4, "status": "tidak", "bukti": [], "alasan": "Tidak ada bukti BigQuery."},
    ]
}


class Jawaban:
    def __init__(self, teks):
        self.choices = [type("C", (), {"message": type("M", (), {"content": teks})()})()]
        self.usage = None


@pytest.fixture
def conn():
    c = repo.connect(":memory:")
    repo.init_db(c)
    h = profil.validasi_profil(profil.baca_file(ROOT / "data_dummy" / "profil.json"))
    profil.muat_ke_db(c, h.profil)
    return c


@pytest.fixture
def client(conn, monkeypatch):
    cfg = replace(muat_config(), penyedia=(PenyediaLLM("utama", "fake/a", rpm=6000, rpd=100),))
    monkeypatch.setattr(llm.LLMClient, "kunci_kurang", staticmethod(lambda p: []))
    return llm.LLMClient(cfg, conn)


def pasang_llm(monkeypatch, *jawaban_cocok):
    """LLM palsu: prompt ekstraksi dijawab EKSTRAKSI, prompt pencocokan dijawab berurutan dari jawaban_cocok."""
    antrean = list(jawaban_cocok) or [PENCOCOKAN]
    panggilan = []

    def palsu(model, messages, **kw):
        isi = messages[0]["content"]
        panggilan.append("ekstrak" if "<data_lowongan>" in isi else "cocok")
        return Jawaban(json.dumps(EKSTRAKSI if panggilan[-1] == "ekstrak" else antrean.pop(0)))

    monkeypatch.setattr(llm.litellm, "completion", palsu)
    return panggilan


# ----- aturan murni -----


@pytest.mark.parametrize(
    "item, skor",
    [
        ([("wajib", "terpenuhi"), ("tambahan", "terpenuhi")], 100),
        ([("wajib", "tidak"), ("tambahan", "tidak")], 0),
        ([("wajib", "terpenuhi"), ("tambahan", "tidak")], 67),  # 2 dari 3
        ([("wajib", "sebagian"), ("wajib", "terpenuhi")], 75),  # (1 + 2) dari 4
        ([], 0),
    ],
)
def test_hitung_skor(item, skor):
    assert pn.hitung_skor(item) == skor


@pytest.mark.parametrize(
    "skor, hasil", [(70, "layak dilamar"), (69, "pertimbangkan"), (50, "pertimbangkan"), (49, "belum cocok")]
)
def test_keputusan(skor, hasil):
    assert pn.keputusan(skor) == hasil


def test_durasi_bulan():
    assert pn.durasi_bulan("2026-07", None, HARI_INI) == 4
    assert pn.durasi_bulan("2025-09", "2025-12", HARI_INI) == 4
    assert pn.durasi_bulan(None, None, HARI_INI) is None


def test_hash_tidak_peka_spasi_dan_huruf_besar():
    assert pn.hash_teks("Data  Analyst\nSQL") == pn.hash_teks("data analyst sql")


def test_klaim_tanpa_bukti_diturunkan():
    hasil = pn.tegakkan_bukti(
        [
            PenilaianRequirement(no=2, status="terpenuhi", bukti=[], alasan="Yakin bisa."),
            PenilaianRequirement(no=1, status="tidak", bukti=["P-01"], alasan="-"),
        ]
    )
    assert [(p.no, p.status, p.bukti) for p in hasil] == [(1, "tidak", []), (2, "tidak", [])]
    assert "diturunkan" in hasil[1].alasan


def test_periksa_pencocokan_menangkap_nomor_dan_id_asing():
    h = HasilPencocokan.model_validate(
        {
            "penilaian": [
                {"no": 1, "status": "terpenuhi", "bukti": ["P-99"]},
                {"no": 1, "status": "tidak"},
                {"no": 5, "status": "tidak"},
            ]
        }
    )
    masalah = " | ".join(pn.periksa_pencocokan(h, 3, {"P-01", "P-02"}))
    assert "belum dinilai: [2, 3]" in masalah
    assert "tidak ada: [5]" in masalah
    assert "lebih dari sekali: [1]" in masalah
    assert "P-99" in masalah


def test_profil_untuk_prompt_tanpa_identitas(conn):
    teks, ids = pn.profil_untuk_prompt(conn, HARI_INI)
    assert "P-03" in ids and len(ids) == 12
    assert "Nama Dummy" not in teks and "contoh.com" not in teks  # nama dan email tidak dikirim ke LLM


# ----- alur lengkap -----


def test_nilai_lowongan_lengkap_dan_tersimpan(conn, client, monkeypatch):
    panggilan = pasang_llm(monkeypatch)
    hasil = pn.nilai_lowongan(client, conn, TEKS, sumber="uji", hari_ini=HARI_INI)

    assert panggilan == ["ekstrak", "cocok"]
    # wajib: 1 + 1 + 0.5 dari 3 (bobot 2) = 5; tambahan: 0 dari 1 (bobot 1); total 5/7
    assert hasil.skor == round(100 * 5 / 7)
    assert hasil.tipe_kerja == "hybrid" and hasil.tenggat == "2026-11-30"
    assert [i.status for i in hasil.item] == ["terpenuhi", "terpenuhi", "sebagian", "tidak"]
    assert repo.daftar_lamaran(conn)[0]["status"] == "ditemukan"
    assert baris_tracker(conn)[0][:3] == ["PT Contoh Digital Indonesia", "Junior Data Analyst", 71]

    pesan = pn.format_pesan(hasil)
    assert "Skor 71/100: LAYAK DILAMAR" in pesan and "30 Nov 2026" in pesan and "P-03" in pesan


def test_lowongan_sama_tidak_dinilai_ulang(conn, client, monkeypatch):
    panggilan = pasang_llm(monkeypatch)
    pertama = pn.nilai_lowongan(client, conn, TEKS, hari_ini=HARI_INI)
    kedua = pn.nilai_lowongan(client, conn, "  " + TEKS.upper(), hari_ini=HARI_INI)
    assert kedua.duplikat and kedua.lowongan_id == pertama.lowongan_id
    assert len(panggilan) == 2  # tidak ada panggilan LLM untuk yang kedua
    assert "sudah pernah dinilai" in pn.format_pesan(kedua)


def test_id_bukti_karangan_diminta_ulang(conn, client, monkeypatch):
    karangan = json.loads(json.dumps(PENCOCOKAN))
    karangan["penilaian"][0]["bukti"] = ["P-77"]
    panggilan = pasang_llm(monkeypatch, karangan, PENCOCOKAN)
    hasil = pn.nilai_lowongan(client, conn, TEKS, hari_ini=HARI_INI)
    assert panggilan == ["ekstrak", "cocok", "cocok"]
    assert hasil.item[0].bukti == ["P-03"]


def test_gagal_di_tengah_tidak_meninggalkan_data(conn, client, monkeypatch):
    karangan = {"penilaian": [{"no": 1, "status": "terpenuhi", "bukti": ["P-77"]}]}
    pasang_llm(monkeypatch, karangan, karangan)
    with pytest.raises(llm.OutputTidakValid):
        pn.nilai_lowongan(client, conn, TEKS, hari_ini=HARI_INI)
    assert conn.execute("SELECT COUNT(*) FROM lowongan").fetchone()[0] == 0


def test_profil_kosong_ditolak(client, monkeypatch):
    kosong = repo.connect(":memory:")
    repo.init_db(kosong)
    pasang_llm(monkeypatch)
    client.conn = kosong
    with pytest.raises(pn.ProfilKosong):
        pn.nilai_lowongan(client, kosong, TEKS, hari_ini=HARI_INI)
