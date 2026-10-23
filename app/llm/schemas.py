"""Schema Pydantic untuk output LLM. Output yang tidak sesuai schema ditolak kode."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Kategori = Literal["wajib", "tambahan"]
JenisRequirement = Literal["skill", "pengalaman", "pendidikan", "bahasa", "sertifikasi", "lainnya"]
StatusCocok = Literal["terpenuhi", "sebagian", "tidak"]


def _huruf_kecil(nilai: Any) -> Any:
    """LLM kadang menulis "Wajib" atau "Hybrid"; samakan sebelum dicocokkan dengan pilihan yang sah."""
    return nilai.strip().lower() if isinstance(nilai, str) else nilai


class Requirement(BaseModel):
    """Satu kualifikasi kandidat. Bila skill berisi lebih dari satu, cukup salah satu yang dikuasai."""

    model_config = ConfigDict(extra="forbid")
    teks_asli: str = Field(min_length=2)
    kategori: Kategori
    jenis: JenisRequirement
    skill: list[str] = []
    tahun_pengalaman: float | None = Field(default=None, ge=0, le=30)

    _rapikan_pilihan = field_validator("kategori", "jenis", mode="before")(_huruf_kecil)

    @field_validator("skill")
    @classmethod
    def _rapikan_skill(cls, nilai: list[str]) -> list[str]:
        hasil: list[str] = []
        for s in nilai:
            s = " ".join(s.lower().split())
            if s and s not in hasil:
                hasil.append(s)
        return hasil


class HasilEkstraksi(BaseModel):
    """Output tugas ekstrak_requirement."""

    model_config = ConfigDict(extra="forbid")
    posisi: str = Field(min_length=2)
    perusahaan: str | None = None
    lokasi: str | None = None
    tipe_kerja: Literal["onsite", "hybrid", "remote"] | None = None
    tenggat: date | None = None
    requirement: list[Requirement] = Field(min_length=1, max_length=40)

    _rapikan_tipe = field_validator("tipe_kerja", mode="before")(_huruf_kecil)


class PenilaianRequirement(BaseModel):
    """Penilaian satu requirement terhadap profil."""

    model_config = ConfigDict(extra="forbid")
    no: int = Field(ge=1)
    status: StatusCocok
    bukti: list[str] = []
    alasan: str = ""

    _rapikan_status = field_validator("status", mode="before")(_huruf_kecil)


class HasilPencocokan(BaseModel):
    """Output tugas cocokkan_profil."""

    model_config = ConfigDict(extra="forbid")
    penilaian: list[PenilaianRequirement] = Field(min_length=1)
