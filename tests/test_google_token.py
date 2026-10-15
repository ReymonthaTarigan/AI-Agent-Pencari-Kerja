"""Token Google harus tersimpan terenkripsi dan bisa dibaca kembali dengan kunci yang sama."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from app.config import muat_config
from app.integrasi import google_api as g


def _cfg(tmp_path, kunci):
    return replace(muat_config(), secrets_dir=tmp_path, token_key=kunci)


def _creds():
    return Credentials(
        token="akses-rahasia",
        refresh_token="refresh-rahasia",
        client_id="cid",
        client_secret="cs",
        token_uri="https://oauth2.googleapis.com/token",
        scopes=g.SCOPES,
        expiry=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),  # library Google memakai UTC tanpa zona
    )


def test_token_terenkripsi_dan_bisa_dibaca(tmp_path):
    cfg = _cfg(tmp_path, Fernet.generate_key().decode())
    g._simpan(cfg, _creds())
    isi = (tmp_path / "google_token.enc").read_bytes()
    assert b"refresh-rahasia" not in isi and b"akses-rahasia" not in isi
    assert g.muat_kredensial(cfg).token == "akses-rahasia"


def test_kunci_berbeda_minta_login_ulang(tmp_path):
    g._simpan(_cfg(tmp_path, Fernet.generate_key().decode()), _creds())
    with pytest.raises(g.PerluLoginUlang):
        g.muat_kredensial(_cfg(tmp_path, Fernet.generate_key().decode()))


def test_tanpa_token_key_ditolak(tmp_path):
    with pytest.raises(g.PerluLoginUlang, match="TOKEN_KEY"):
        g.muat_kredensial(_cfg(tmp_path, ""))
