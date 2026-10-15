"""Koneksi ke Google: Gmail, Calendar, Drive, Docs, Sheets.

Izin dibuat sesempit mungkin:
- gmail.modify            : baca email, beri label, buat draf, kirim (pengiriman tetap lewat persetujuan)
- calendar.app.created    : hanya kalender yang dibuat aplikasi ini ("Cari Kerja") beserta event-nya
- calendar.freebusy       : hanya melihat kapan kamu sibuk/kosong, tanpa isi event
- drive.file              : hanya file dan folder yang dibuat aplikasi ini (termasuk Docs dan Sheets)

Token login disimpan TERENKRIPSI di secrets/google_token.enc dengan kunci TOKEN_KEY dari .env.
Di mode "testing" Google, token kedaluwarsa sekitar 7 hari; bila itu terjadi,
jalankan ulang: python -m scripts.uji_koneksi_google --login
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import Config
from app.db import repo

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.app.created",
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/drive.file",
]

NAMA_KALENDER = "Cari Kerja"
NAMA_TRACKER = "Tracker Lamaran (Agent)"
NAMA_FOLDER = "Lamaran (Agent)"
KOLOM_LAMARAN = ["Perusahaan", "Posisi", "Skor", "Status", "Tenggat", "Link lowongan", "Folder", "Diperbarui"]
KOLOM_LOG = ["Waktu", "Kejadian"]


class PerluLoginUlang(Exception):
    """Token tidak ada atau sudah kedaluwarsa dan perlu login lewat browser."""


# ===== Kredensial =====


def _fernet(cfg: Config) -> Fernet:
    if not cfg.token_key:
        raise PerluLoginUlang("TOKEN_KEY belum diisi di .env. Jalankan: python -m scripts.buat_kunci")
    return Fernet(cfg.token_key.encode())


def _path_token(cfg: Config) -> Path:
    return cfg.secrets_dir / "google_token.enc"


def _simpan(cfg: Config, creds: Credentials) -> None:
    cfg.secrets_dir.mkdir(parents=True, exist_ok=True)
    _path_token(cfg).write_bytes(_fernet(cfg).encrypt(creds.to_json().encode()))


def muat_kredensial(cfg: Config, interaktif: bool = False) -> Credentials:
    """Ambil kredensial dari disk; perbarui atau minta login bila perlu."""
    fernet = _fernet(cfg)  # gagal lebih awal dengan pesan jelas bila TOKEN_KEY kosong
    creds = None
    path = _path_token(cfg)
    if path.exists():
        try:
            info = json.loads(fernet.decrypt(path.read_bytes()))
            creds = Credentials.from_authorized_user_info(info, SCOPES)
        except InvalidToken:
            creds = None  # TOKEN_KEY berganti: token lama tidak bisa dibaca, perlu login ulang

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _simpan(cfg, creds)
            return creds
        except RefreshError:
            creds = None  # biasanya refresh token 7 hari di mode testing sudah lewat

    if not interaktif:
        raise PerluLoginUlang("Perlu login Google. Jalankan: python -m scripts.uji_koneksi_google --login")
    if not cfg.google_client_secret.exists():
        raise FileNotFoundError(
            f"File OAuth client tidak ditemukan: {cfg.google_client_secret}. "
            "Unduh dari Google Cloud Console (tipe Desktop app) dan simpan di lokasi itu."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow  # diimpor di sini karena hanya dipakai saat login

    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.google_client_secret), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _simpan(cfg, creds)
    return creds


class Google:
    """Kumpulan layanan Google yang sudah terautentikasi."""

    def __init__(self, cfg: Config, creds: Credentials):
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.zona_waktu)
        self.gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.docs = build("docs", "v1", credentials=creds, cache_discovery=False)

    # ----- Gmail -----

    def email_terbaru(self, jumlah: int = 5) -> list[dict]:
        """Ambil pengirim, subjek, dan tanggal (tanpa isi) dari beberapa email terbaru."""
        hasil = self.gmail.users().messages().list(userId="me", maxResults=jumlah).execute()
        daftar = []
        for m in hasil.get("messages", []):
            detail = (
                self.gmail.users()
                .messages()
                .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"])
                .execute()
            )
            header = {h["name"]: h["value"] for h in detail["payload"].get("headers", [])}
            daftar.append(
                {"dari": header.get("From", ""), "subjek": header.get("Subject", ""), "tanggal": header.get("Date", "")}
            )
        return daftar

    # ----- Calendar -----

    def pastikan_kalender(self, conn: sqlite3.Connection) -> str:
        """Kembalikan id kalender "Cari Kerja"; buat bila belum ada atau sudah dihapus."""
        cal_id = repo.get_pengaturan(conn, "calendar_id")
        if cal_id:
            try:
                self.calendar.calendars().get(calendarId=cal_id).execute()
                return cal_id
            except HttpError as e:
                if e.resp.status not in (403, 404):
                    raise
        baru = (
            self.calendar.calendars()
            .insert(
                body={
                    "summary": NAMA_KALENDER,
                    "timeZone": self.cfg.zona_waktu,
                    "description": "Dibuat oleh agent asisten cari kerja.",
                }
            )
            .execute()
        )
        repo.set_pengaturan(conn, "calendar_id", baru["id"])
        return baru["id"]

    def buat_event(self, calendar_id: str, judul: str, mulai: datetime, menit: int, deskripsi: str = "") -> dict:
        selesai = mulai + timedelta(minutes=menit)
        return (
            self.calendar.events()
            .insert(
                calendarId=calendar_id,
                body={
                    "summary": judul,
                    "description": deskripsi,
                    "start": {"dateTime": mulai.isoformat(), "timeZone": self.cfg.zona_waktu},
                    "end": {"dateTime": selesai.isoformat(), "timeZone": self.cfg.zona_waktu},
                },
            )
            .execute()
        )

    # ----- Drive -----

    def pastikan_folder_induk(self, conn: sqlite3.Connection) -> str:
        folder_id = repo.get_pengaturan(conn, "drive_folder_id")
        if folder_id:
            try:
                f = self.drive.files().get(fileId=folder_id, fields="id, trashed").execute()
                if not f.get("trashed"):
                    return folder_id
            except HttpError as e:
                if e.resp.status != 404:
                    raise
        baru = (
            self.drive.files()
            .create(body={"name": NAMA_FOLDER, "mimeType": "application/vnd.google-apps.folder"}, fields="id")
            .execute()
        )
        repo.set_pengaturan(conn, "drive_folder_id", baru["id"])
        return baru["id"]

    # ----- Sheets -----

    def pastikan_tracker(self, conn: sqlite3.Connection) -> str:
        """Kembalikan id spreadsheet tracker; buat dengan tab Lamaran dan Log bila belum ada."""
        sheet_id = repo.get_pengaturan(conn, "tracker_spreadsheet_id")
        if sheet_id:
            try:
                f = self.drive.files().get(fileId=sheet_id, fields="id, trashed").execute()
                if not f.get("trashed"):
                    return sheet_id
            except HttpError as e:
                if e.resp.status != 404:
                    raise
        baru = (
            self.sheets.spreadsheets()
            .create(
                body={
                    "properties": {"title": NAMA_TRACKER, "timeZone": self.cfg.zona_waktu},
                    "sheets": [
                        {"properties": {"title": "Lamaran", "gridProperties": {"frozenRowCount": 1}}},
                        {"properties": {"title": "Log", "gridProperties": {"frozenRowCount": 1}}},
                    ],
                },
                fields="spreadsheetId",
            )
            .execute()
        )
        sheet_id = baru["spreadsheetId"]
        self.sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": "Lamaran!A1", "values": [KOLOM_LAMARAN]},
                    {"range": "Log!A1", "values": [KOLOM_LOG]},
                ],
            },
        ).execute()
        repo.set_pengaturan(conn, "tracker_spreadsheet_id", sheet_id)
        return sheet_id

    def tambah_log_sheet(self, sheet_id: str, kejadian: str) -> None:
        waktu = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M")
        self.sheets.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range="Log!A:B",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[waktu, kejadian]]},
        ).execute()
