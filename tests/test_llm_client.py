from dataclasses import replace

import pytest

from app.config import Config, PenyediaLLM, muat_config
from app.db import repo
from app.llm import client as llm
from app.llm.schemas import HasilEkstraksi
from app.workflows.penilaian import PROMPT_EKSTRAKSI

JSON_BAIK = (
    '{"posisi": "Data Analyst", "perusahaan": null, "requirement": '
    '[{"teks_asli": "Menguasai SQL", "kategori": "wajib", "jenis": "skill", "skill": ["sql"]}]}'
)


@pytest.fixture
def conn():
    c = repo.connect(":memory:")
    repo.init_db(c)
    return c


@pytest.fixture
def cfg() -> Config:
    return replace(
        muat_config(),
        penyedia=(
            PenyediaLLM("utama", "fake/a", rpm=6000, rpd=10),
            PenyediaLLM("cadangan", "fake/b", rpm=6000, rpd=10),
        ),
        kuota_henti=0.9,
    )


@pytest.fixture(autouse=True)
def kunci_dianggap_ada(monkeypatch):
    monkeypatch.setattr(llm.LLMClient, "kunci_kurang", staticmethod(lambda p: []))


class Jawaban:
    def __init__(self, teks):
        self.choices = [type("C", (), {"message": type("M", (), {"content": teks})()})()]
        self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()


@pytest.mark.parametrize(
    "mentah",
    [
        JSON_BAIK,
        f"```json\n{JSON_BAIK}\n```",
        f"Berikut hasilnya:\n{JSON_BAIK}\nSemoga membantu.",
    ],
)
def test_bersihkan_json(mentah):
    assert HasilEkstraksi.model_validate_json(llm.bersihkan_json(mentah)).posisi == "Data Analyst"


def test_pindah_ke_cadangan_saat_utama_gagal(monkeypatch, cfg, conn):
    def palsu(model, **kw):
        if model == "fake/a":
            raise RuntimeError("429 kuota habis")
        return Jawaban(JSON_BAIK)

    monkeypatch.setattr(llm.litellm, "completion", palsu)
    hasil = llm.LLMClient(cfg, conn).panggil_json("t", [{"role": "user", "content": "x"}], HasilEkstraksi)
    assert hasil.requirement[0].skill == ["sql"]
    assert repo.hitung_request_24jam(conn, "utama") == 1  # kegagalan tetap dihitung
    assert repo.hitung_request_24jam(conn, "cadangan") == 1


def test_coba_ulang_sekali_lalu_berhasil(monkeypatch, cfg, conn):
    jawaban = iter(['{"posisi": "DA"}', JSON_BAIK])
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: Jawaban(next(jawaban)))
    hasil = llm.LLMClient(cfg, conn).panggil_json("t", [{"role": "user", "content": "x"}], HasilEkstraksi)
    assert hasil.posisi == "Data Analyst"


def test_tetap_tidak_valid_menghasilkan_error(monkeypatch, cfg, conn):
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: Jawaban("bukan json"))
    with pytest.raises(llm.OutputTidakValid):
        llm.LLMClient(cfg, conn).panggil_json("t", [{"role": "user", "content": "x"}], HasilEkstraksi)


def test_penyedia_dilewati_saat_kuota_hampir_habis(monkeypatch, cfg, conn):
    for _ in range(9):  # 9 dari 10 = 90%, sama dengan ambang henti
        repo.catat_llm(conn, tugas="t", penyedia="utama", model="fake/a", sukses=True)
    dipanggil = []
    monkeypatch.setattr(llm.litellm, "completion", lambda model, **kw: dipanggil.append(model) or Jawaban(JSON_BAIK))
    llm.LLMClient(cfg, conn).panggil("t", [{"role": "user", "content": "x"}])
    assert dipanggil == ["fake/b"]


def test_semua_kuota_habis(monkeypatch, cfg, conn):
    for nama in ("utama", "cadangan"):
        for _ in range(10):
            repo.catat_llm(conn, tugas="t", penyedia=nama, model="m", sukses=True)
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: pytest.fail("tidak boleh dipanggil"))
    with pytest.raises(llm.SemuaPenyediaGagal):
        llm.LLMClient(cfg, conn).panggil("t", [{"role": "user", "content": "x"}], prioritas="mendesak")


def test_prompt_dimuat_dengan_isian():
    teks = llm.muat_prompt(f"{PROMPT_EKSTRAKSI}.md", lowongan="LOWONGAN UJI", hari_ini="2026-10-15")
    assert "LOWONGAN UJI" in teks and "2026-10-15" in teks and "{{" not in teks


def test_penyedia_tanpa_api_key_dilewati_tanpa_memakai_kuota(monkeypatch, cfg, conn):
    monkeypatch.setattr(llm.LLMClient, "kunci_kurang", staticmethod(lambda p: ["KUNCI_A"] if p.nama == "utama" else []))
    dipanggil = []
    monkeypatch.setattr(llm.litellm, "completion", lambda model, **kw: dipanggil.append(model) or Jawaban(JSON_BAIK))
    llm.LLMClient(cfg, conn).panggil("t", [{"role": "user", "content": "x"}])
    assert dipanggil == ["fake/b"]
    assert repo.hitung_request_24jam(conn, "utama") == 0


def test_ringkas_error_menyatukan_json_dan_membuang_traceback():
    e = RuntimeError(
        'GeminiException - {\n  "error": {\n    "code": 404,\n    "message": "model not found"\n  }\n}\n'
        'Traceback (most recent call last):\n  File "x.py"'
    )
    hasil = llm.ringkas_error(e)
    assert "model not found" in hasil and "Traceback" not in hasil and "\n" not in hasil


@pytest.mark.parametrize(
    "model, harapan",
    [
        ("gemini/gemini-3.5-flash", False),
        ("gemini/gemini-3-flash-preview", False),
        ("gemini/gemini-10-flash", False),
        ("gemini/gemini-2.5-flash", True),
        ("gemini/gemma-4-31b-it", True),
        ("groq/openai/gpt-oss-120b", True),
    ],
)
def test_temperature_hanya_untuk_model_yang_mendukung(model, harapan):
    assert llm.dukung_temperature(model) is harapan


def test_validasi_isi_memicu_coba_ulang(monkeypatch, cfg, conn):
    jawaban = iter([JSON_BAIK.replace('"sql"', '"cobol"'), JSON_BAIK])
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: Jawaban(next(jawaban)))

    def hanya_sql(h):
        return [] if h.requirement[0].skill == ["sql"] else ["skill harus sql"]

    hasil = llm.LLMClient(cfg, conn).panggil_json(
        "t", [{"role": "user", "content": "x"}], HasilEkstraksi, validasi=hanya_sql
    )
    assert hasil.requirement[0].skill == ["sql"]
