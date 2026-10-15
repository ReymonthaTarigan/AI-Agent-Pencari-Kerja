-- Skema database agent asisten cari kerja.
-- SQLite adalah sumber kebenaran; Google Sheets hanya tampilan.
-- Waktu disimpan dalam UTC, format ISO 8601 (datetime('now')).
-- Aman dijalankan berulang kali (IF NOT EXISTS).

PRAGMA foreign_keys = ON;

-- ===== Profil dan bukti =====

CREATE TABLE IF NOT EXISTS skill (
    id            TEXT PRIMARY KEY,                -- S-01, S-02, ...
    nama_normal   TEXT NOT NULL UNIQUE,            -- huruf kecil, mis. "sql"
    alias         TEXT NOT NULL DEFAULT '[]',      -- JSON array, mis. ["mysql","postgresql"]
    level         TEXT NOT NULL CHECK (level IN ('dasar', 'menengah', 'mahir'))
);

CREATE TABLE IF NOT EXISTS profil_item (
    id            TEXT PRIMARY KEY,                -- P-01, P-02, ...
    jenis         TEXT NOT NULL CHECK (jenis IN (
                      'pendidikan', 'pengalaman_kerja', 'magang', 'proyek_magang',
                      'proyek_kuliah', 'proyek_pribadi', 'organisasi',
                      'sertifikat', 'pencapaian')),
    judul         TEXT NOT NULL,
    organisasi    TEXT,
    mulai         TEXT,                            -- YYYY-MM
    selesai       TEXT,                            -- YYYY-MM, NULL bila masih berjalan
    deskripsi     TEXT NOT NULL,
    bukti_angka   TEXT NOT NULL DEFAULT '[]',      -- JSON array berisi pernyataan faktual
    link          TEXT
);

-- Skill hanya sah jika didukung minimal satu item profil.
CREATE TABLE IF NOT EXISTS profil_item_skill (
    item_id       TEXT NOT NULL REFERENCES profil_item(id) ON DELETE CASCADE,
    skill_id      TEXT NOT NULL REFERENCES skill(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, skill_id)
);

-- ===== Lowongan dan penilaian =====

CREATE TABLE IF NOT EXISTS lowongan (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sumber           TEXT NOT NULL,                -- manual, halaman_karier, ...
    url              TEXT,
    perusahaan       TEXT,
    posisi           TEXT NOT NULL,
    lokasi           TEXT,
    tipe_kerja       TEXT,                         -- onsite, hybrid, remote
    gaji_teks        TEXT,
    tenggat          TEXT,                         -- YYYY-MM-DD
    deskripsi        TEXT NOT NULL,
    hash_deskripsi   TEXT NOT NULL UNIQUE,         -- mencegah duplikat
    ditemukan_pada   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS requirement (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    lowongan_id        INTEGER NOT NULL REFERENCES lowongan(id) ON DELETE CASCADE,
    teks_asli          TEXT NOT NULL,
    kategori           TEXT NOT NULL CHECK (kategori IN ('wajib', 'tambahan')),
    skill_normal       TEXT,
    tahun_pengalaman   REAL
);

CREATE TABLE IF NOT EXISTS kecocokan (
    lowongan_id      INTEGER NOT NULL REFERENCES lowongan(id) ON DELETE CASCADE,
    requirement_id   INTEGER NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
    status           TEXT NOT NULL CHECK (status IN ('terpenuhi', 'sebagian', 'tidak')),
    bukti_item_ids   TEXT NOT NULL DEFAULT '[]',   -- JSON array id profil_item
    dinilai_pada     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (lowongan_id, requirement_id)
);

-- ===== Lamaran =====

CREATE TABLE IF NOT EXISTS lamaran (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    lowongan_id            INTEGER NOT NULL UNIQUE REFERENCES lowongan(id),
    status                 TEXT NOT NULL DEFAULT 'ditemukan' CHECK (status IN (
                               'ditemukan', 'disiapkan', 'menunggu_persetujuan', 'terkirim',
                               'dihubungi', 'interview', 'tawaran', 'ditolak',
                               'tidak_ada_kabar', 'dilewati')),
    skor_kecocokan         REAL,
    folder_drive_url       TEXT,
    versi_cv               TEXT,
    dikirim_pada           TEXT,
    kontak_terakhir_pada   TEXT,
    dibuat_pada            TEXT NOT NULL DEFAULT (datetime('now')),
    diperbarui_pada        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_lamaran (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lamaran_id   INTEGER NOT NULL REFERENCES lamaran(id) ON DELETE CASCADE,
    waktu        TEXT NOT NULL DEFAULT (datetime('now')),
    jenis        TEXT NOT NULL,                    -- status_berubah, email_masuk, ...
    sumber       TEXT NOT NULL CHECK (sumber IN ('email', 'manual', 'agent')),
    catatan      TEXT
);

CREATE TABLE IF NOT EXISTS dokumen (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lamaran_id       INTEGER NOT NULL REFERENCES lamaran(id) ON DELETE CASCADE,
    jenis            TEXT NOT NULL CHECK (jenis IN ('cv', 'cover_letter', 'brief', 'laporan')),
    doc_url          TEXT,
    pdf_url          TEXT,
    validasi_lolos   INTEGER NOT NULL DEFAULT 0,
    alasan_gagal     TEXT,
    dibuat_pada      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sesi_interview (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lamaran_id    INTEGER REFERENCES lamaran(id) ON DELETE SET NULL,
    waktu         TEXT NOT NULL DEFAULT (datetime('now')),
    mode          TEXT NOT NULL CHECK (mode IN ('teks', 'suara')),
    pertanyaan    TEXT NOT NULL,
    jawaban       TEXT,
    skor_rubrik   TEXT                             -- JSON
);

-- ===== Persetujuan (human-in-the-loop) =====

CREATE TABLE IF NOT EXISTS persetujuan (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    jenis_aksi         TEXT NOT NULL,              -- kirim_email, kirim_lamaran
    payload_hash       TEXT NOT NULL,              -- hash isi yang disetujui
    status             TEXT NOT NULL DEFAULT 'menunggu' CHECK (status IN (
                           'menunggu', 'disetujui', 'ditolak', 'kedaluwarsa', 'dipakai')),
    diminta_pada       TEXT NOT NULL DEFAULT (datetime('now')),
    dijawab_pada       TEXT,
    kedaluwarsa_pada   TEXT NOT NULL
);

-- ===== Log dan pengaturan =====

CREATE TABLE IF NOT EXISTS log_llm (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    waktu          TEXT NOT NULL DEFAULT (datetime('now')),
    tugas          TEXT NOT NULL,
    penyedia       TEXT NOT NULL,
    model          TEXT NOT NULL,
    versi_prompt   TEXT,
    token_in       INTEGER,
    token_out      INTEGER,
    latensi_ms     INTEGER,
    sukses         INTEGER NOT NULL,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_llm_penyedia_waktu ON log_llm (penyedia, waktu);

CREATE TABLE IF NOT EXISTS log_tool (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    waktu            TEXT NOT NULL DEFAULT (datetime('now')),
    tool             TEXT NOT NULL,
    input_ringkas    TEXT,
    hasil_ringkas    TEXT,
    error            TEXT,
    persetujuan_id   INTEGER REFERENCES persetujuan(id)
);

-- Nilai kecil yang perlu diingat: id kalender, id spreadsheet tracker, dsb.
CREATE TABLE IF NOT EXISTS pengaturan (
    kunci            TEXT PRIMARY KEY,
    nilai            TEXT NOT NULL,
    diperbarui_pada  TEXT NOT NULL DEFAULT (datetime('now'))
);
