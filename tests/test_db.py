import sqlite3

import pytest

from app.db import repo


@pytest.fixture
def conn():
    c = repo.connect(":memory:")
    repo.init_db(c)
    return c


def test_init_db_aman_diulang(conn):
    repo.init_db(conn)
    tabel = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("profil_item", "skill", "lowongan", "lamaran", "persetujuan", "log_llm", "pengaturan"):
        assert t in tabel


def test_status_lamaran_tidak_dikenal_ditolak(conn):
    conn.execute("INSERT INTO lowongan (sumber, posisi, deskripsi, hash_deskripsi) VALUES ('manual','DA','x','h1')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO lamaran (lowongan_id, status) VALUES (1, 'terkirim_diam_diam')")


def test_lowongan_duplikat_ditolak(conn):
    sql = "INSERT INTO lowongan (sumber, posisi, deskripsi, hash_deskripsi) VALUES ('manual','DA','x','sama')"
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_pengaturan_simpan_dan_timpa(conn):
    repo.set_pengaturan(conn, "calendar_id", "abc")
    repo.set_pengaturan(conn, "calendar_id", "xyz")
    assert repo.get_pengaturan(conn, "calendar_id") == "xyz"
    assert repo.get_pengaturan(conn, "tidak_ada", "bawaan") == "bawaan"


def test_hitung_request_hanya_24_jam_terakhir(conn):
    for _ in range(3):
        repo.catat_llm(conn, tugas="t", penyedia="utama", model="m", sukses=True)
    conn.execute(
        "INSERT INTO log_llm (waktu, tugas, penyedia, model, sukses) "
        "VALUES (datetime('now','-2 days'), 't', 'utama', 'm', 1)"
    )
    repo.catat_llm(conn, tugas="t", penyedia="cadangan", model="m", sukses=False)
    assert repo.hitung_request_24jam(conn, "utama") == 3
    assert repo.hitung_request_24jam(conn, "cadangan") == 1
