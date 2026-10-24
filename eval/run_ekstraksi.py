"""Evaluasi prompt ekstraksi requirement terhadap dataset berlabel.

    python -m eval.run_ekstraksi                     # semua kasus, penyedia sesuai urutan di .env
    python -m eval.run_ekstraksi --penyedia cadangan # hanya satu penyedia
    python -m eval.run_ekstraksi --ulang 2           # tiap kasus dijalankan 2 kali untuk melihat konsistensi
    python -m eval.run_ekstraksi --prompt ekstrak_requirement_v3  # versi prompt lain, untuk membandingkan
    python -m eval.run_ekstraksi --kasus asli        # hanya kasus yang namanya diawali "asli"

Setiap kasus terdiri dari dua file bernama sama di eval/datasets/ekstraksi/:
- <nama>.txt  : teks lowongan apa adanya
- <nama>.json : jawaban yang diharapkan, dengan format:
    {"requirement": [{"kategori": "wajib", "skill": ["sql"]},              # cocok bila skill-nya persis ini
                     {"kategori": "wajib", "skill": ["excel|microsoft excel"]},  # "|" = ejaan lain yang diterima
                     {"kategori": "tambahan", "kunci": ["1 tahun"]}],      # cocok bila teks_asli memuat salah satunya
     "dilarang": ["30 november"]}                                          # tidak boleh muncul di requirement mana pun

Satu kasus memakai 1-2 request LLM per pengulangan. Hasil lengkap disimpan di eval/hasil/ (tidak masuk repo).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from app.config import ROOT, get_config
from app.db import repo
from app.llm.client import LLMClient, OutputTidakValid, SemuaPenyediaGagal
from app.llm.schemas import HasilEkstraksi, Requirement
from app.workflows.penilaian import PROMPT_EKSTRAKSI, ekstrak

DATASET = ROOT / "eval" / "datasets" / "ekstraksi"
FOLDER_HASIL = ROOT / "eval" / "hasil"
TARGET_AKURASI = 0.85


def _normal(teks: str) -> str:
    return " ".join(teks.lower().split())


def cocok(harapan: dict, pred: Requirement) -> bool:
    if "skill" in harapan:
        dimiliki = {_normal(s) for s in pred.skill}
        diminta = [{_normal(a) for a in s.split("|")} for s in harapan["skill"]]
        return len(dimiliki) == len(diminta) and all(dimiliki & alternatif for alternatif in diminta)
    teks = _normal(pred.teks_asli)
    return any(_normal(k) in teks for k in harapan["kunci"])


def _label(harapan: dict) -> str:
    return f"[{harapan['kategori']}] " + (
        "skill " + "/".join(harapan["skill"])
        if "skill" in harapan
        else "teks memuat " + " atau ".join(harapan["kunci"])
    )


def nilai_kasus(harapan: dict, hasil: HasilEkstraksi) -> dict:
    """Bandingkan satu hasil ekstraksi dengan harapan. Pencocokan satu-satu: tiap requirement dipakai sekali."""
    sisa = list(hasil.requirement)
    benar, salah_kategori, hilang = 0, [], []
    # Harapan berbasis skill dicocokkan lebih dulu karena lebih spesifik daripada pencarian kata kunci.
    for h in sorted(harapan["requirement"], key=lambda h: "skill" not in h):
        idx = next((i for i, p in enumerate(sisa) if cocok(h, p)), None)
        if idx is None:
            hilang.append(_label(h))
            continue
        p = sisa.pop(idx)
        if p.kategori == h["kategori"]:
            benar += 1
        else:
            salah_kategori.append(f"{_label(h)} terbaca sebagai {p.kategori}")
    dilarang = (
        [p.teks_asli for p in hasil.requirement if any(_normal(d) in _normal(p.teks_asli) for d in harapan["dilarang"])]
        if harapan.get("dilarang")
        else []
    )
    return {
        "harapan": len(harapan["requirement"]),
        "benar": benar,
        "salah_kategori": salah_kategori,
        "hilang": hilang,
        "ekstra": [p.teks_asli for p in sisa],
        "dilarang": dilarang,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--penyedia", help="utama, cadangan, atau lokal")
    parser.add_argument("--ulang", type=int, default=1)
    parser.add_argument("--prompt", default=PROMPT_EKSTRAKSI, help="nama file prompt tanpa .md")
    parser.add_argument("--kasus", default="", help="awalan nama kasus, misalnya asli atau contoh")
    args = parser.parse_args()

    kasus = sorted(DATASET.glob(f"{args.kasus}*.json"))
    if not kasus:
        print(f"Tidak ada kasus di {DATASET}")
        return 1
    cfg = get_config()
    conn = repo.connect(cfg.db_path)
    repo.init_db(conn)
    client = LLMClient(cfg, conn, hanya=[args.penyedia] if args.penyedia else None)
    hari_ini = date.today()

    laporan, total_harapan, total_benar, total_dilarang, gagal = [], 0, 0, 0, 0
    per_kelompok: dict[str, list[int]] = {}  # awalan nama kasus (asli, contoh) -> [benar, harapan]
    print(f"Prompt: {args.prompt} | {len(kasus)} kasus x {args.ulang} pengulangan\n")
    for file_json in kasus:
        harapan = json.loads(file_json.read_text(encoding="utf-8"))
        teks = file_json.with_suffix(".txt").read_text(encoding="utf-8")
        for ulang in range(1, args.ulang + 1):
            label = f"{file_json.stem}#{ulang}" if args.ulang > 1 else file_json.stem
            try:
                hasil = ekstrak(client, teks, hari_ini, versi_prompt=args.prompt)
            except (SemuaPenyediaGagal, OutputTidakValid) as e:
                gagal += 1
                total_harapan += len(harapan["requirement"])
                per_kelompok.setdefault(file_json.stem.split("_")[0], [0, 0])[1] += len(harapan["requirement"])
                print(f"[GAGAL] {label}: {e}")
                laporan.append({"kasus": label, "gagal": str(e)})
                continue
            n = nilai_kasus(harapan, hasil)
            total_harapan += n["harapan"]
            total_benar += n["benar"]
            kelompok = per_kelompok.setdefault(file_json.stem.split("_")[0], [0, 0])
            kelompok[0] += n["benar"]
            kelompok[1] += n["harapan"]
            total_dilarang += len(n["dilarang"])
            tanda = "OK" if n["benar"] == n["harapan"] and not n["dilarang"] else "CEK"
            print(
                f"[{tanda}] {label}: {n['benar']}/{n['harapan']} benar, "
                f"{len(n['ekstra'])} ekstra, {len(n['dilarang'])} dilarang"
            )
            for judul, isi in (
                ("hilang", n["hilang"]),
                ("kategori salah", n["salah_kategori"]),
                ("dilarang muncul", n["dilarang"]),
                ("ekstra", n["ekstra"]),
            ):
                for x in isi:
                    print(f"      {judul}: {x}")
            laporan.append({"kasus": label, **n, "hasil": hasil.model_dump(mode="json")})

    akurasi = total_benar / total_harapan if total_harapan else 0.0
    lolos = akurasi >= TARGET_AKURASI and total_dilarang == 0 and not gagal
    print(f"\nAkurasi requirement: {total_benar}/{total_harapan} = {akurasi:.0%} (target {TARGET_AKURASI:.0%})")
    if len(per_kelompok) > 1:
        for nama, (benar, harapan) in sorted(per_kelompok.items()):
            print(f"  kasus {nama}: {benar}/{harapan} = {benar / harapan:.0%}")
    print(f"Requirement terlarang yang muncul: {total_dilarang} (target 0) | Kasus gagal: {gagal}")
    print("LOLOS" if lolos else "BELUM LOLOS")

    FOLDER_HASIL.mkdir(parents=True, exist_ok=True)
    nama = FOLDER_HASIL / f"ekstraksi_{datetime.now():%Y%m%d_%H%M%S}.json"
    nama.write_text(
        json.dumps(
            {"prompt": args.prompt, "akurasi": akurasi, "per_kelompok": per_kelompok, "kasus": laporan},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Detail: {nama.relative_to(ROOT)}")
    conn.close()
    return 0 if lolos else 1


if __name__ == "__main__":
    sys.exit(main())
