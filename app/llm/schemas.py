"""Schema Pydantic untuk output LLM. Output yang tidak sesuai schema ditolak kode."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teks_asli: str = Field(min_length=2)
    kategori: Literal["wajib", "tambahan"]
    skill_normal: str | None = None
    tahun_pengalaman: float | None = Field(default=None, ge=0, le=30)


class HasilEkstraksi(BaseModel):
    """Output tugas ekstrak_requirement (dipakai penuh di Tahap 1, dipakai uji di Tahap 0)."""

    model_config = ConfigDict(extra="forbid")
    posisi: str = Field(min_length=2)
    perusahaan: str | None = None
    requirement: list[Requirement] = Field(min_length=1)
