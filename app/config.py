"""Konfigurasi aplikasi, dibaca dari file .env di akar proyek.

Semua rahasia (token, API key) hanya dibaca dari sini, tidak pernah ditulis di kode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _str(nama: str, default: str = "") -> str:
    return (os.getenv(nama) or default).strip()


def _int(nama: str, default: int | None) -> int | None:
    nilai = _str(nama)
    if not nilai:
        return default
    try:
        return int(nilai)
    except ValueError:
        raise ValueError(f"{nama} di .env harus berupa angka bulat, bukan {nilai!r}") from None


def _float(nama: str, default: float) -> float:
    nilai = _str(nama)
    if not nilai:
        return default
    try:
        return float(nilai)
    except ValueError:
        raise ValueError(f"{nama} di .env harus berupa angka, bukan {nilai!r}") from None


def _path(nama: str, default: str) -> Path:
    p = Path(_str(nama, default))
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class PenyediaLLM:
    nama: str  # label untuk log: "utama", "cadangan", "lokal"
    model: str  # string model LiteLLM, mis. "gemini/gemini-3.5-flash-lite"
    rpm: int  # batas request per menit yang kita patuhi
    rpd: int  # batas request per 24 jam yang kita patuhi


@dataclass(frozen=True)
class Config:
    telegram_token: str
    owner_chat_id: int | None
    telegram_proxy: str
    penyedia: tuple[PenyediaLLM, ...]
    kuota_peringatan: float
    kuota_henti: float
    google_client_secret: Path
    token_key: str
    db_path: Path
    profil_path: Path
    secrets_dir: Path
    zona_waktu: str


def muat_config() -> Config:
    penyedia = []
    for label in ("UTAMA", "CADANGAN", "LOKAL"):
        model = _str(f"LLM_{label}_MODEL")
        if model:
            penyedia.append(
                PenyediaLLM(
                    nama=label.lower(),
                    model=model,
                    rpm=_int(f"LLM_{label}_RPM", 10),
                    rpd=_int(f"LLM_{label}_RPD", 200),
                )
            )
    return Config(
        telegram_token=_str("TELEGRAM_BOT_TOKEN"),
        owner_chat_id=_int("TELEGRAM_OWNER_CHAT_ID", None),
        telegram_proxy=_str("TELEGRAM_PROXY"),
        penyedia=tuple(penyedia),
        kuota_peringatan=_float("KUOTA_PERINGATAN", 0.8),
        kuota_henti=_float("KUOTA_HENTI", 0.9),
        google_client_secret=_path("GOOGLE_CLIENT_SECRET", "secrets/client_secret.json"),
        token_key=_str("TOKEN_KEY"),
        db_path=_path("DB_PATH", "data/agent.db"),
        profil_path=_path("PROFIL_PATH", "data_dummy/profil.json"),
        secrets_dir=ROOT / "secrets",
        zona_waktu=_str("ZONA_WAKTU", "Asia/Jakarta"),
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    return muat_config()
