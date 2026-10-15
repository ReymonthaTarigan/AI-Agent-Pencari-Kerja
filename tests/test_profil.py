import copy

import pytest

from app.config import ROOT
from app.db import profil, repo

DUMMY = profil.baca_file(ROOT / "data_dummy" / "profil.json")


@pytest.fixture
def data():
    return copy.deepcopy(DUMMY)


def test_profil_dummy_valid_dan_cukup_untuk_gerbang(data):
    h = profil.validasi_profil(data)
    assert h.valid, h.error
    assert h.jumlah_item_lengkap >= 10


def test_item_tanpa_bukti_diberi_peringatan(data):
    h = profil.validasi_profil(data)
    assert any(p.startswith("P-12") for p in h.peringatan)


def test_skill_tanpa_bukti_ditolak(data):
    data["skill"].append({"id": "S-99", "nama": "Kubernetes", "level": "mahir"})
    h = profil.validasi_profil(data)
    assert not h.valid
    assert any("S-99" in e and "tidak didukung" in e for e in h.error)


def test_skill_ganda_dalam_satu_item_ditolak(data):
    data["item"][0]["skill"] = ["S-09", "S-09"]
    h = profil.validasi_profil(data)
    assert any("lebih dari sekali" in e and "S-09" in e for e in h.error)


def test_rujukan_skill_tidak_dikenal_ditolak(data):
    data["item"][0]["skill"].append("S-77")
    h = profil.validasi_profil(data)
    assert any("S-77" in e for e in h.error)


def test_id_duplikat_ditolak(data):
    data["item"][1]["id"] = data["item"][0]["id"]
    h = profil.validasi_profil(data)
    assert any("duplikat" in e for e in h.error)


def test_kolom_salah_ketik_ditolak(data):
    data["item"][0]["buktiangka"] = ["salah ketik"]
    h = profil.validasi_profil(data)
    assert not h.valid


def test_tanggal_terbalik_ditolak(data):
    data["item"][2]["mulai"], data["item"][2]["selesai"] = "2026-09", "2026-08"
    h = profil.validasi_profil(data)
    assert any("setelah selesai" in e for e in h.error)


def test_muat_ke_db_dan_ulang_tanpa_duplikat(data):
    conn = repo.connect(":memory:")
    repo.init_db(conn)
    h = profil.validasi_profil(data)
    profil.muat_ke_db(conn, h.profil)
    profil.muat_ke_db(conn, h.profil)  # dijalankan dua kali: isi diganti, bukan ditambah
    s = repo.statistik_profil(conn)
    assert s == {"item": 12, "item_lengkap": 11, "skill": 10}
    assert repo.get_pengaturan(conn, "identitas")["nama"] == "Nama Dummy"
