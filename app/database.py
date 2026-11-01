import os
import sqlite3
from pathlib import Path

_data_dir = os.environ.get("HF_DATA_DIR", "/app/config")
DB_PATH = os.environ.get("HF_DB_PATH", str(Path(_data_dir) / "hailfrequency.db"))

# Command history rows kept after startup pruning
HISTORY_MAX_ROWS = 1000

# Learned voice phrases kept after startup pruning (LRU by last_used_at)
LEARNED_MAX_ROWS = 500


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = get_db()
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS media_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                user_id TEXT,
                user_name TEXT,
                is_default INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS device_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                pattern TEXT NOT NULL,
                FOREIGN KEY (server_id) REFERENCES media_servers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS command_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                transcript TEXT,
                intent_json TEXT,
                action TEXT,
                result TEXT,
                success INTEGER DEFAULT 1,
                parse_source TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_history_timestamp
                ON command_history(timestamp DESC);

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                role TEXT NOT NULL DEFAULT 'user',
                password_hash TEXT,
                password_salt TEXT,
                password_iterations INTEGER,
                theme TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT
            );

            CREATE TABLE IF NOT EXISTS user_identities (
                user_id TEXT NOT NULL,
                server_id TEXT NOT NULL,
                media_user_id TEXT NOT NULL,
                media_username TEXT,
                media_token TEXT,
                PRIMARY KEY (user_id, server_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (server_id) REFERENCES media_servers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS learned_phrases (
                normalized_phrase TEXT PRIMARY KEY,
                intent_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                last_used_at TEXT DEFAULT (datetime('now')),
                use_count INTEGER DEFAULT 0
            );
        """)
        _migrate(db)
        # Prune history so the table doesn't grow forever
        db.execute(
            "DELETE FROM command_history WHERE id NOT IN "
            "(SELECT id FROM command_history ORDER BY timestamp DESC, id DESC LIMIT ?)",
            (HISTORY_MAX_ROWS,),
        )
        db.execute(
            "DELETE FROM learned_phrases WHERE normalized_phrase NOT IN "
            "(SELECT normalized_phrase FROM learned_phrases "
            "ORDER BY last_used_at DESC LIMIT ?)",
            (LEARNED_MAX_ROWS,),
        )
        db.commit()
    finally:
        db.close()
    # Tighten existing database files to owner-only; the file holds
    # media-server API keys and access tokens in plaintext
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def _migrate(db: sqlite3.Connection) -> None:
    """Idempotent upgrades for existing databases."""
    import uuid

    # command_history.user_id (multi-user)
    cols = {r[1] for r in db.execute("PRAGMA table_info(command_history)")}
    if "user_id" not in cols:
        db.execute("ALTER TABLE command_history ADD COLUMN user_id TEXT")
    if "parse_source" not in cols:
        db.execute("ALTER TABLE command_history ADD COLUMN parse_source TEXT")

    # users.disabled (admin can cut off a user)
    ucols = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    if "disabled" not in ucols:
        db.execute("ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0")

    # Drop servers of any type the adapters cannot drive (aliases and
    # identities cascade), and make sure a default server remains.
    if db.execute(
        "SELECT COUNT(*) FROM media_servers "
        "WHERE type NOT IN ('emby', 'jellyfin')"
    ).fetchone()[0]:
        db.execute(
            "DELETE FROM media_servers WHERE type NOT IN ('emby', 'jellyfin')"
        )
        if not db.execute(
            "SELECT COUNT(*) FROM media_servers WHERE is_default = 1"
        ).fetchone()[0]:
            db.execute(
                "UPDATE media_servers SET is_default = 1 WHERE id = "
                "(SELECT id FROM media_servers ORDER BY created_at LIMIT 1)"
            )

    # A database that predates the users table keeps its admin in settings;
    # move it across so session cookies (keyed by username) stay valid
    has_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if not has_users:
        def s(key):
            row = db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None

        username, pw_hash = s("username"), s("password_hash")
        if username and pw_hash:
            db.execute(
                "INSERT INTO users (id, username, role, password_hash, "
                "password_salt, password_iterations, theme) "
                "VALUES (?, ?, 'admin', ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex, username, pw_hash,
                    s("password_salt"),
                    int(s("password_iterations") or 100_000),
                    s("ui_theme") or "lcars",
                ),
            )
