"""Tampilkan model yang benar-benar bisa dipakai oleh API key-mu.

    python -m scripts.daftar_model

Daftar diambil langsung dari Gemini dan Groq memakai key di .env (tidak memakai kuota
request model). Salin salah satu nama yang tercetak ke LLM_UTAMA_MODEL / LLM_CADANGAN_MODEL.
"""

from __future__ import annotations

import os
import sys

import httpx

import app.config  # noqa: F401  (memuat .env ke environment)

TIMEOUT = 20
BUKAN_CHAT = ("whisper", "tts", "playai", "orpheus", "guard", "embedding", "imagen", "veo", "aqa")


def daftar_gemini(key: str) -> list[str]:
    hasil, token = [], None
    while True:
        params = {"pageSize": 1000}
        if token:
            params["pageToken"] = token
        r = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
            params=params,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        for m in data.get("models", []):
            nama = m["name"].removeprefix("models/")
            if "generateContent" in m.get("supportedGenerationMethods", []) and not any(b in nama for b in BUKAN_CHAT):
                hasil.append(f"gemini/{nama}")
        token = data.get("nextPageToken")
        if not token:
            return sorted(hasil)


def daftar_groq(key: str) -> list[str]:
    r = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
    r.raise_for_status()
    return sorted(
        f"groq/{m['id']}"
        for m in r.json().get("data", [])
        if m.get("active", True) and not any(b in m["id"] for b in BUKAN_CHAT)
    )


def tampilkan(judul: str, nama_env: str, fungsi) -> bool:
    print(f"\n=== {judul} ===")
    key = (os.getenv(nama_env) or "").strip()
    if not key:
        print(f"[LEWATI] {nama_env} belum diisi di .env")
        return True
    try:
        model = fungsi(key)
    except httpx.HTTPStatusError as e:
        print(f"[GAGAL] Ditolak server (HTTP {e.response.status_code}). Periksa {nama_env}.")
        return False
    except httpx.HTTPError as e:
        print(f"[GAGAL] Tidak bisa terhubung ({type(e).__name__}). Periksa jaringan.")
        return False
    if not model:
        print("[PERINGATAN] Tidak ada model chat yang tersedia untuk key ini.")
    for m in model:
        print(f"  {m}")
    return True


def main() -> int:
    ok = tampilkan("Gemini (GEMINI_API_KEY)", "GEMINI_API_KEY", daftar_gemini)
    ok = tampilkan("Groq (GROQ_API_KEY)", "GROQ_API_KEY", daftar_groq) and ok
    print("\nSalin nama di atas apa adanya (termasuk awalan gemini/ atau groq/) ke .env.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
