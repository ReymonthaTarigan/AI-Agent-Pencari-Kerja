"""Buat kunci enkripsi untuk token Google.

    python -m scripts.buat_kunci
Salin hasilnya ke TOKEN_KEY di .env. Jangan dibagikan; bila kunci diganti,
token lama tidak bisa dibaca dan kamu perlu login Google ulang.
"""

from cryptography.fernet import Fernet

if __name__ == "__main__":
    print(Fernet.generate_key().decode())
