"""Satu-satunya pintu ke LLM.

Tanggung jawab modul ini:
- memanggil penyedia sesuai urutan di .env (utama -> cadangan -> lokal);
- mematuhi batas per menit (jeda antar request, tanpa pemanggilan paralel);
- menghitung pemakaian 24 jam dan berhenti sebelum kuota habis;
- mencatat setiap panggilan ke tabel log_llm;
- memvalidasi output JSON dengan schema Pydantic dan mencoba ulang sekali bila tidak valid.
LiteLLM tidak diizinkan mencoba ulang sendiri (num_retries=0) agar kuota tidak terbuang diam-diam.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from app.config import Config, PenyediaLLM
from app.db import repo

litellm.drop_params = True  # buang parameter yang tidak didukung penyedia tertentu
litellm.suppress_debug_info = True

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
T = TypeVar("T", bound=BaseModel)
Prioritas = Literal["normal", "mendesak"]


class SemuaPenyediaGagal(Exception):
    """Tidak ada penyedia yang berhasil (gagal, atau kuotanya habis)."""


class OutputTidakValid(Exception):
    """Output LLM tetap tidak sesuai schema setelah dicoba ulang."""


def muat_prompt(nama_file: str, **isian: str) -> str:
    """Baca file prompt lalu ganti penanda {{NAMA}} dengan isian."""
    teks = (PROMPT_DIR / nama_file).read_text(encoding="utf-8")
    for kunci, nilai in isian.items():
        teks = teks.replace("{{" + kunci.upper() + "}}", nilai)
    return teks


def bersihkan_json(teks: str) -> str:
    """Ambil objek JSON dari jawaban LLM yang kadang dibungkus ``` atau diberi kalimat pembuka."""
    teks = teks.strip()
    teks = re.sub(r"^```(?:json)?\s*|\s*```$", "", teks, flags=re.IGNORECASE).strip()
    awal, akhir = teks.find("{"), teks.rfind("}")
    if awal != -1 and akhir > awal:
        return teks[awal : akhir + 1]
    return teks


def dukung_temperature(model: str) -> bool:
    """Gemini 3 ke atas menyarankan agar temperature tidak lagi dikirim."""
    return not re.match(r"^gemini/gemini-([3-9]|\d{2,})", model)


def ringkas_error(e: Exception, maks: int = 300) -> str:
    """Pesan error satu baris: buang traceback yang ikut terbawa, rapatkan spasi."""
    teks = str(e).split("Traceback (most recent call last)")[0]
    return " ".join(teks.split())[:maks]


class LLMClient:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, hanya: Sequence[str] | None = None):
        self.cfg = cfg
        self.conn = conn
        self.penyedia = [p for p in cfg.penyedia if not hanya or p.nama in hanya]
        self._terakhir: dict[str, float] = {}

    # ----- kuota -----

    def status_kuota(self) -> list[dict]:
        hasil = []
        for p in self.penyedia:
            dipakai = repo.hitung_request_24jam(self.conn, p.nama)
            hasil.append(
                {
                    "penyedia": p.nama,
                    "model": p.model,
                    "dipakai": dipakai,
                    "batas": p.rpd,
                    "persen": round(100 * dipakai / p.rpd, 1) if p.rpd else 0.0,
                }
            )
        return hasil

    @staticmethod
    def kunci_kurang(p: PenyediaLLM) -> list[str]:
        """Nama variabel API key yang belum diisi untuk penyedia ini (kosong = siap)."""
        if p.model.startswith("ollama/"):
            return []
        env = litellm.validate_environment(model=p.model)
        return [] if env.get("keys_in_environment") else list(env.get("missing_keys", []))

    def _boleh_dipakai(self, p: PenyediaLLM, prioritas: Prioritas) -> bool:
        ambang = 1.0 if prioritas == "mendesak" else self.cfg.kuota_henti
        return repo.hitung_request_24jam(self.conn, p.nama) < p.rpd * ambang

    def _tunggu_giliran(self, p: PenyediaLLM) -> None:
        jeda = 60.0 / max(p.rpm, 1)
        terakhir = self._terakhir.get(p.nama)
        if terakhir is not None:
            sisa = jeda - (time.monotonic() - terakhir)
            if sisa > 0:
                time.sleep(sisa)
        self._terakhir[p.nama] = time.monotonic()

    # ----- pemanggilan -----

    def panggil(
        self,
        tugas: str,
        messages: list[dict],
        *,
        versi_prompt: str | None = None,
        json_mode: bool = False,
        prioritas: Prioritas = "normal",
    ) -> tuple[str, PenyediaLLM]:
        """Kirim messages ke penyedia pertama yang tersedia. Mengembalikan (teks, penyedia)."""
        if not self.penyedia:
            raise SemuaPenyediaGagal("Belum ada model LLM di .env (LLM_UTAMA_MODEL, dst).")
        alasan = []
        for p in self.penyedia:
            kurang = self.kunci_kurang(p)
            if kurang:
                alasan.append(f"{p.nama}: API key belum diisi ({', '.join(kurang)})")
                continue
            if not self._boleh_dipakai(p, prioritas):
                alasan.append(f"{p.nama}: kuota 24 jam hampir habis")
                continue
            self._tunggu_giliran(p)
            mulai = time.monotonic()
            try:
                extra: dict = {"response_format": {"type": "json_object"}} if json_mode else {}
                if dukung_temperature(p.model):
                    extra["temperature"] = 0  # jawaban sekonsisten mungkin
                resp = litellm.completion(model=p.model, messages=messages, timeout=60, num_retries=0, **extra)
                teks = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                repo.catat_llm(
                    self.conn,
                    tugas=tugas,
                    penyedia=p.nama,
                    model=p.model,
                    sukses=True,
                    versi_prompt=versi_prompt,
                    token_in=getattr(usage, "prompt_tokens", None),
                    token_out=getattr(usage, "completion_tokens", None),
                    latensi_ms=int((time.monotonic() - mulai) * 1000),
                )
                return teks, p
            except Exception as e:  # semua error penyedia diperlakukan sama: catat, lalu coba penyedia berikutnya
                pesan = f"{type(e).__name__}: {ringkas_error(e)}"
                repo.catat_llm(
                    self.conn,
                    tugas=tugas,
                    penyedia=p.nama,
                    model=p.model,
                    sukses=False,
                    versi_prompt=versi_prompt,
                    latensi_ms=int((time.monotonic() - mulai) * 1000),
                    error=pesan[:500],
                )
                alasan.append(f"{p.nama}: {pesan[:200]}")
        raise SemuaPenyediaGagal(" | ".join(alasan))

    def panggil_json(
        self,
        tugas: str,
        messages: list[dict],
        schema: type[T],
        *,
        versi_prompt: str | None = None,
        maks_ulang: int = 1,
        prioritas: Prioritas = "normal",
    ) -> T:
        """Panggil LLM dan kembalikan objek yang sudah lolos validasi schema."""
        pesan = list(messages)
        for percobaan in range(maks_ulang + 1):
            teks, _ = self.panggil(tugas, pesan, versi_prompt=versi_prompt, json_mode=True, prioritas=prioritas)
            try:
                return schema.model_validate_json(bersihkan_json(teks))
            except (ValidationError, json.JSONDecodeError) as e:
                if percobaan == maks_ulang:
                    raise OutputTidakValid(f"Output tidak sesuai schema {schema.__name__}: {e}") from e
                pesan = [
                    *pesan,
                    {"role": "assistant", "content": teks},
                    {
                        "role": "user",
                        "content": (
                            "Output sebelumnya tidak sesuai format. Kesalahan:\n"
                            f"{str(e)[:1500]}\n"
                            "Kirim ulang HANYA objek JSON yang valid sesuai format yang diminta."
                        ),
                    },
                ]
        raise AssertionError("tidak terjangkau")
