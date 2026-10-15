import pytest

from app.config import muat_config


def test_angka_tidak_valid_di_env_diberi_pesan_jelas(monkeypatch):
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "12345abc")
    with pytest.raises(ValueError, match="TELEGRAM_OWNER_CHAT_ID"):
        muat_config()


def test_chat_id_kosong_berarti_mode_setup(monkeypatch):
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "")
    assert muat_config().owner_chat_id is None


def test_penyedia_tanpa_model_tidak_dipakai(monkeypatch):
    monkeypatch.setenv("LLM_UTAMA_MODEL", "gemini/model-uji")
    monkeypatch.setenv("LLM_CADANGAN_MODEL", "")
    monkeypatch.setenv("LLM_LOKAL_MODEL", "")
    assert [p.nama for p in muat_config().penyedia] == ["utama"]
