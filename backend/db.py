import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "index.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    parent_dir TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    atime REAL NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    regenerable INTEGER NOT NULL DEFAULT 0,
    scanned_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_parent ON files(parent_dir);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size DESC);
CREATE INDEX IF NOT EXISTS idx_files_atime ON files(atime);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    last_activity REAL NOT NULL,
    source_size INTEGER DEFAULT 0,
    artifact_size INTEGER DEFAULT 0,
    scanned_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY,
    original_path TEXT NOT NULL,
    quarantine_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    category TEXT,
    quarantined_at REAL NOT NULL,
    purge_after REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'quarantined'
);
CREATE INDEX IF NOT EXISTS idx_quarantine_status ON quarantine(status);

CREATE TABLE IF NOT EXISTS scan_meta (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,
    files_indexed INTEGER DEFAULT 0,
    current_path TEXT,
    total_size INTEGER DEFAULT 0,
    error TEXT,
    app_signals TEXT       -- JSON: {has_steam, has_xcode, has_docker, ...}
);

CREATE TABLE IF NOT EXISTS apps_index (
    bundle_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    path TEXT NOT NULL,
    version TEXT,
    last_used_date REAL,
    scanned_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apps_display ON apps_index(lower(display_name));

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    signature TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'high',
    source TEXT NOT NULL,
    model TEXT,
    created_at REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_labels_sig ON labels(signature);

CREATE TABLE IF NOT EXISTS warmup_cache (
    key TEXT PRIMARY KEY,        -- e.g. 'overview', 'suggestions', 'sunburst'
    value TEXT NOT NULL,         -- JSON
    scan_id INTEGER NOT NULL,    -- references scan_meta.id
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS redundancies (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    group_key TEXT NOT NULL,
    keep_path TEXT,
    redundant_path TEXT NOT NULL,
    size_freed INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL,
    detail TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_red_type ON redundancies(type);
CREATE INDEX IF NOT EXISTS idx_red_group ON redundancies(group_key);
"""


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Migrations for older DBs
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scan_meta)").fetchall()}
        if "app_signals" not in cols:
            conn.execute("ALTER TABLE scan_meta ADD COLUMN app_signals TEXT")
        if "index_version" not in cols:
            conn.execute("ALTER TABLE scan_meta ADD COLUMN index_version INTEGER DEFAULT 1")
        # Per-file AI verdict cache. Re-clicks on the same file are instant.
        fcols = {r["name"] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
        if "ai_verdict" not in fcols:
            conn.execute("ALTER TABLE files ADD COLUMN ai_verdict TEXT")
        if "ai_reason" not in fcols:
            conn.execute("ALTER TABLE files ADD COLUMN ai_reason TEXT")
        if "ai_verdict_at" not in fcols:
            conn.execute("ALTER TABLE files ADD COLUMN ai_verdict_at INTEGER")
        if "ai_confidence" not in fcols:
            conn.execute("ALTER TABLE files ADD COLUMN ai_confidence INTEGER")
        conn.execute("DROP TABLE IF EXISTS llm_cache")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
