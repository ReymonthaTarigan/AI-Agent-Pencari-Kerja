"""Logika penilaian di skrip evaluasi, supaya angka akurasinya bisa dipercaya."""

from app.llm.schemas import HasilEkstraksi
from eval.run_ekstraksi import nilai_kasus


def _hasil(*req):
    return HasilEkstraksi.model_validate({"posisi": "DA", "requirement": list(req)})


def _r(teks, kategori="wajib", skill=()):
    return {"teks_asli": teks, "kategori": kategori, "jenis": "skill" if skill else "lainnya", "skill": list(skill)}


HARAPAN = {
    "requirement": [
        {"kategori": "wajib", "skill": ["looker studio", "tableau", "power bi"]},
        {"kategori": "wajib", "skill": ["excel|microsoft excel"]},
        {"kategori": "tambahan", "kunci": ["1 tahun"]},
    ],
    "dilarang": ["30 november"],
}


def test_semua_benar():
    n = nilai_kasus(
        HARAPAN,
        _hasil(
            _r("Dashboard", skill=["Power BI", "tableau", "looker studio"]),
            _r("Excel", skill=["microsoft excel"]),
            _r("Pengalaman 1 tahun", "tambahan"),
        ),
    )
    assert (n["benar"], n["hilang"], n["ekstra"], n["dilarang"]) == (3, [], [], [])


def test_atau_yang_dipecah_dihitung_salah():
    n = nilai_kasus(
        HARAPAN,
        _hasil(
            _r("Looker", skill=["looker studio"]),
            _r("Tableau", skill=["tableau"]),
            _r("Power BI", skill=["power bi"]),
            _r("Excel", skill=["excel"]),
            _r("Pengalaman 1 tahun", "tambahan"),
        ),
    )
    assert n["benar"] == 2 and len(n["hilang"]) == 1 and len(n["ekstra"]) == 3


def test_kategori_salah_dan_dilarang():
    n = nilai_kasus(
        HARAPAN,
        _hasil(
            _r("Dashboard", skill=["looker studio", "tableau", "power bi"]),
            _r("Excel", skill=["excel"]),
            _r("Pengalaman 1 tahun", "wajib"),
            _r("Kirim lamaran paling lambat 30 November"),
        ),
    )
    assert n["benar"] == 2 and len(n["salah_kategori"]) == 1 and len(n["dilarang"]) == 1
