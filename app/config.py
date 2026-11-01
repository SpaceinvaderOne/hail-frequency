import uuid

from .database import get_db


def get_setting(key: str) -> str | None:
    db = get_db()
    try:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        db.close()


def get_settings_batch(keys: list[str]) -> dict[str, str | None]:
    """Fetch several settings in one connection/query. Missing keys map to None."""
    if not keys:
        return {}
    db = get_db()
    try:
        placeholders = ",".join("?" * len(keys))
        rows = db.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            list(keys),
        ).fetchall()
        found = {row["key"]: row["value"] for row in rows}
        return {k: found.get(k) for k in keys}
    finally:
        db.close()


def set_setting(key: str, value: str) -> None:
    db = get_db()
    try:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        db.commit()
    finally:
        db.close()


def is_setup_complete() -> bool:
    """Always reads from database, never cached."""
    return get_setting("setup_complete") == "true"


def get_media_servers() -> list[dict]:
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM media_servers ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def get_media_server(server_id: str) -> dict | None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM media_servers WHERE id = ?", (server_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def get_default_server() -> dict | None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM media_servers WHERE is_default = 1"
        ).fetchone()
        if row:
            return dict(row)
        # Fall back to first server if none marked default
        row = db.execute(
            "SELECT * FROM media_servers ORDER BY created_at LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def add_media_server(
    name: str,
    server_type: str,
    url: str,
    api_key: str,
    user_id: str | None = None,
    user_name: str | None = None,
    is_default: bool = True,
) -> str:
    server_id = uuid.uuid4().hex
    db = get_db()
    try:
        if is_default:
            db.execute("UPDATE media_servers SET is_default = 0")
        db.execute(
            "INSERT INTO media_servers (id, name, type, url, api_key, user_id, user_name, is_default) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (server_id, name, server_type, url, api_key, user_id, user_name, 1 if is_default else 0),
        )
        db.commit()
        return server_id
    finally:
        db.close()


def update_media_server(server_id: str, **fields) -> bool:
    """Update specific fields on a media server record."""
    if not fields:
        return False
    allowed = {"name", "url", "api_key", "user_id", "user_name", "is_default"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    db = get_db()
    try:
        # Only one server may be default — clear the flag on the others first
        if updates.get("is_default"):
            db.execute(
                "UPDATE media_servers SET is_default = 0 WHERE id != ?", (server_id,)
            )
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [server_id]
        cursor = db.execute(
            f"UPDATE media_servers SET {set_clause} WHERE id = ?",
            values,
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def get_device_aliases(server_id: str) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM device_aliases WHERE server_id = ?", (server_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def get_all_device_aliases() -> list[dict]:
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM device_aliases").fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def add_device_alias(server_id: str, alias: str, pattern: str) -> int:
    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO device_aliases (server_id, alias, pattern) VALUES (?, ?, ?)",
            (server_id, alias.lower(), pattern),
        )
        db.commit()
        return cursor.lastrowid
    finally:
        db.close()


def remove_device_alias(alias: str) -> bool:
    db = get_db()
    try:
        cursor = db.execute(
            "DELETE FROM device_aliases WHERE alias = ?", (alias.lower(),)
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def clear_device_aliases(server_id: str) -> None:
    """Remove all device aliases for a server (before re-saving)."""
    db = get_db()
    try:
        db.execute("DELETE FROM device_aliases WHERE server_id = ?", (server_id,))
        db.commit()
    finally:
        db.close()


def delete_media_server(server_id: str) -> bool:
    """Delete a media server (device_aliases cascade via FK)."""
    db = get_db()
    try:
        cursor = db.execute("DELETE FROM media_servers WHERE id = ?", (server_id,))
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def log_command(
    transcript: str | None,
    intent_json: str | None,
    action: str | None,
    result: str | None,
    success: bool = True,
    user_id: str | None = None,
    parse_source: str | None = None,
) -> None:
    db = get_db()
    try:
        db.execute(
            "INSERT INTO command_history "
            "(transcript, intent_json, action, result, success, user_id, parse_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (transcript, intent_json, action, result, 1 if success else 0, user_id, parse_source),
        )
        db.commit()
    finally:
        db.close()


def get_command_history(
    limit: int = 50, offset: int = 0, user_id: str | None = None
) -> list[dict]:
    """History rows, newest first. user_id filters to one user (None = all,
    for admins), with username joined in for display."""
    db = get_db()
    try:
        where = "WHERE ch.user_id = ?" if user_id else ""
        params: list = [user_id] if user_id else []
        rows = db.execute(
            f"SELECT ch.*, u.username FROM command_history ch "
            f"LEFT JOIN users u ON ch.user_id = u.id {where} "
            f"ORDER BY ch.timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


# ── Users & identities ──────────────────────────────────────────────


def get_local_admin() -> dict | None:
    """The local (password-holding) admin account, if one exists."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM users WHERE role = 'admin' "
            "AND password_hash IS NOT NULL LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def get_user_by_username(username: str) -> dict | None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def create_user(
    username: str,
    role: str = "user",
    password_hash: str | None = None,
    password_salt: str | None = None,
    password_iterations: int | None = None,
    theme: str = "lcars",
) -> dict:
    user_id = uuid.uuid4().hex
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (id, username, role, password_hash, "
            "password_salt, password_iterations, theme) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, role, password_hash, password_salt,
             password_iterations, theme),
        )
        db.commit()
        return dict(db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone())
    finally:
        db.close()


def touch_user_login(user_id: str) -> None:
    db = get_db()
    try:
        db.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?",
            (user_id,),
        )
        db.commit()
    finally:
        db.close()


def update_user_theme(user_id: str, theme: str) -> None:
    db = get_db()
    try:
        db.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user_id))
        db.commit()
    finally:
        db.close()


def update_user_password(
    user_id: str, password_hash: str, password_salt: str, iterations: int
) -> None:
    db = get_db()
    try:
        db.execute(
            "UPDATE users SET password_hash = ?, password_salt = ?, "
            "password_iterations = ? WHERE id = ?",
            (password_hash, password_salt, iterations, user_id),
        )
        db.commit()
    finally:
        db.close()


def reset_local_admin(
    username: str, password_hash: str, password_salt: str, iterations: int
) -> dict:
    """Replace the local admin account (setup step 1, re-runnable)."""
    db = get_db()
    try:
        db.execute(
            "DELETE FROM users WHERE role = 'admin' AND password_hash IS NOT NULL"
        )
        user_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO users (id, username, role, password_hash, "
            "password_salt, password_iterations, theme) "
            "VALUES (?, ?, 'admin', ?, ?, ?, 'lcars')",
            (user_id, username, password_hash, password_salt, iterations),
        )
        db.commit()
        return dict(db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone())
    finally:
        db.close()


def list_users() -> list[dict]:
    """All app users with linked identities — for the admin Users panel.
    Password material is never included."""
    db = get_db()
    try:
        users = []
        for row in db.execute(
            "SELECT id, username, role, theme, disabled, created_at, "
            "last_login, password_hash IS NOT NULL AS is_local "
            "FROM users ORDER BY created_at"
        ):
            u = dict(row)
            u["is_local"] = bool(u["is_local"])
            u["disabled"] = bool(u["disabled"])
            u["identities"] = [
                dict(i) for i in db.execute(
                    "SELECT ui.server_id, ui.media_user_id, ui.media_username, "
                    "ms.name AS server_name, ms.type AS server_type "
                    "FROM user_identities ui "
                    "JOIN media_servers ms ON ui.server_id = ms.id "
                    "WHERE ui.user_id = ?",
                    (u["id"],),
                )
            ]
            users.append(u)
        return users
    finally:
        db.close()


def set_user_role(user_id: str, role: str) -> bool:
    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def set_user_disabled(user_id: str, disabled: bool) -> bool:
    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE users SET disabled = ? WHERE id = ?",
            (1 if disabled else 0, user_id),
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def delete_user(user_id: str) -> bool:
    """Delete a user (identities cascade; their sessions die on next request)."""
    db = get_db()
    try:
        cursor = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def get_user_by_id(user_id: str) -> dict | None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def get_identity(user_id: str, server_id: str) -> dict | None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM user_identities WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def get_user_identities(user_id: str) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM user_identities WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def upsert_identity(
    user_id: str,
    server_id: str,
    media_user_id: str,
    media_username: str | None,
    media_token: str | None,
) -> None:
    db = get_db()
    try:
        db.execute(
            "INSERT INTO user_identities "
            "(user_id, server_id, media_user_id, media_username, media_token) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, server_id) DO UPDATE SET "
            "media_user_id = excluded.media_user_id, "
            "media_username = excluded.media_username, "
            "media_token = excluded.media_token",
            (user_id, server_id, media_user_id, media_username, media_token),
        )
        db.commit()
    finally:
        db.close()


# ── Learned voice phrases ───────────────────────────────────────────


def get_learned_intent(phrase: str) -> str | None:
    """Stored intent JSON for an exact normalized phrase, or None.
    Bumps LRU metadata on hit."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT intent_json FROM learned_phrases "
            "WHERE normalized_phrase = ?", (phrase,)
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE learned_phrases SET last_used_at = datetime('now'), "
            "use_count = use_count + 1 WHERE normalized_phrase = ?", (phrase,)
        )
        db.commit()
        return row["intent_json"]
    finally:
        db.close()


def learn_phrase(phrase: str, intent_json: str) -> None:
    db = get_db()
    try:
        db.execute(
            "INSERT INTO learned_phrases (normalized_phrase, intent_json) "
            "VALUES (?, ?) ON CONFLICT(normalized_phrase) DO UPDATE SET "
            "intent_json = excluded.intent_json, "
            "last_used_at = datetime('now')",
            (phrase, intent_json),
        )
        db.commit()
    finally:
        db.close()


def evict_learned_phrase(phrase: str) -> None:
    db = get_db()
    try:
        db.execute(
            "DELETE FROM learned_phrases WHERE normalized_phrase = ?",
            (phrase,),
        )
        db.commit()
    finally:
        db.close()


def clear_learned_phrases() -> int:
    db = get_db()
    try:
        cur = db.execute("DELETE FROM learned_phrases")
        db.commit()
        return cur.rowcount
    finally:
        db.close()


def count_learned_phrases() -> int:
    db = get_db()
    try:
        return db.execute(
            "SELECT COUNT(*) FROM learned_phrases"
        ).fetchone()[0]
    finally:
        db.close()


def get_learned_phrases() -> list[dict]:
    """All learned phrases, newest-used first (table is capped at
    LEARNED_MAX_ROWS so no pagination is needed)."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT normalized_phrase, intent_json, use_count, last_used_at "
            "FROM learned_phrases "
            "ORDER BY last_used_at DESC, normalized_phrase"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def redact_api_key(key: str) -> str:
    if not key or len(key) < 4:
        return "********"
    return "********" + key[-4:]


def generate_server_id() -> str:
    return uuid.uuid4().hex
