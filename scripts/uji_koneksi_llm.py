"""Uji koneksi ke setiap penyedia LLM di .env dengan tugas ekstraksi requirement.

    python -m scripts.uji_koneksi_llm                 # uji semua penyedia (1-2 request per penyedia)
    python -m scripts.uji_koneksi_llm --penyedia utama
Setiap penyedia diuji terpisah supaya kelihatan mana yang berfungsi.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from app.config import ROOT, get_config
from app.db import repo
from app.llm.client import LLMClient, OutputTidakValid, SemuaPenyediaGagal
from app.workflows.penilaian import ekstrak


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--penyedia", help="utama, cadangan, atau lokal")
    args = parser.parse_args()

    cfg = get_config()
    conn = repo.connect(cfg.db_path)
    repo.init_db(conn)

    target = [p for p in cfg.penyedia if not args.penyedia or p.nama == args.penyedia]
    if not target:
        print("[GAGAL] Tidak ada penyedia yang cocok. Isi LLM_UTAMA_MODEL dan seterusnya di .env.")
        return 1

    lowongan = (ROOT / "data_dummy" / "lowongan_contoh.txt").read_text(encoding="utf-8")
    gagal = 0
    for p in target:
        print(f"\n=== {p.nama}: {p.model} ===")
        client = LLMClient(cfg, conn, hanya=[p.nama])
        mulai = time.monotonic()
        try:
            hasil = ekstrak(client, lowongan, date.today())
        except (SemuaPenyediaGagal, OutputTidakValid) as e:
            gagal += 1
            print(f"[GAGAL] {e}")
            continue
        print(f"[OK] {time.monotonic() - mulai:.1f} detik. Posisi: {hasil.posisi} | Perusahaan: {hasil.perusahaan}")
        for r in hasil.requirement:
            skill = " / ".join(r.skill) or "-"
            print(f"  - [{r.kategori}] ({r.jenis}) {r.teks_asli}  ->  {skill}")

    print("\nPemakaian 24 jam:")
    for k in LLMClient(cfg, conn).status_kuota():
        print(f"  {k['penyedia']}: {k['dipakai']}/{k['batas']} request")
    conn.close()
    return 1 if gagal else 0


if __name__ == "__main__":
    sys.exit(main())
