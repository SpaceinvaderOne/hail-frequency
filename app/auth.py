import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import (
    get_local_admin,
    get_setting,
    get_user_by_username,
    is_setup_complete,
)

# ── Password hashing ────────────────────────────────────────────────

PBKDF2_ITERATIONS = 600_000  # OWASP recommendation for PBKDF2-SHA256
# Accepted for hashes stored without an iteration count; such hashes are
# re-hashed at PBKDF2_ITERATIONS on the next successful login
LEGACY_PBKDF2_ITERATIONS = 100_000


def hash_password(
    password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS
) -> tuple[str, str]:
    """Hash password with PBKDF2-SHA256. Returns (hash_hex, salt_hex)."""
    if salt is None:
        salt = os.urandom(32)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return pw_hash.hex(), salt.hex()


def verify_password(
    password: str,
    stored_hash: str,
    salt_hex: str,
    iterations: int = PBKDF2_ITERATIONS,
) -> bool:
    """Verify password using constant-time comparison."""
    salt = bytes.fromhex(salt_hex)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return secrets.compare_digest(pw_hash.hex(), stored_hash)


def get_stored_iterations() -> int:
    """Iteration count of the stored hash (LEGACY_PBKDF2_ITERATIONS if unset)."""
    stored = get_setting("password_iterations")
    try:
        return int(stored) if stored else LEGACY_PBKDF2_ITERATIONS
    except ValueError:
        return LEGACY_PBKDF2_ITERATIONS


def verify_stored_password(password: str) -> bool:
    """Verify a password against the stored hash, honoring its iteration count."""
    stored_hash = get_setting("password_hash")
    salt = get_setting("password_salt")
    if not stored_hash or not salt:
        return False
    return verify_password(password, stored_hash, salt, get_stored_iterations())


def verify_user_password(user: dict, password: str) -> bool:
    """Verify a password against a users-table row (local accounts only)."""
    if not user or not user.get("password_hash") or not user.get("password_salt"):
        return False
    iterations = user.get("password_iterations") or LEGACY_PBKDF2_ITERATIONS
    return verify_password(
        password, user["password_hash"], user["password_salt"], iterations
    )


def require_admin(request: Request) -> None:
    """FastAPI dependency: 403 unless the session user is an admin."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


def maybe_reset_admin_password() -> None:
    """Lockout recovery: if HF_ADMIN_RESET_PASSWORD is set at startup,
    reset the local admin's password to it. The operator must remove the
    variable again afterwards — a loud warning is logged either way.
    """
    import logging

    from .config import get_local_admin, set_setting, update_user_password

    new_pw = os.environ.get("HF_ADMIN_RESET_PASSWORD")
    if not new_pw:
        return
    log = logging.getLogger("hailfrequency")
    if len(new_pw) < 8:
        log.warning("HF_ADMIN_RESET_PASSWORD ignored: must be 8+ characters")
        return
    admin = get_local_admin()
    if not admin:
        log.warning("HF_ADMIN_RESET_PASSWORD set but no local admin exists yet")
        return
    pw_hash, salt = hash_password(new_pw)
    update_user_password(admin["id"], pw_hash, salt, PBKDF2_ITERATIONS)
    set_setting("password_hash", pw_hash)
    set_setting("password_salt", salt)
    set_setting("password_iterations", str(PBKDF2_ITERATIONS))
    # Same rule as a Settings password change: a new signing key signs
    # every existing session out. A recovery reset that left old sessions
    # alive would be no recovery at all.
    set_setting("hmac_key", secrets.token_hex(32))
    log.warning(
        "ADMIN PASSWORD RESET via HF_ADMIN_RESET_PASSWORD for '%s'; all "
        "sessions signed out. Log in, change the password, and REMOVE the "
        "variable from the container config now.", admin["username"],
    )


# ── Session tokens ──────────────────────────────────────────────────

SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days


def create_session_token(username: str, hmac_key: str) -> str:
    """Create HMAC-SHA256 signed session token: timestamp:username:signature."""
    timestamp = str(int(time.time()))
    payload = f"{timestamp}:{username}"
    signature = hmac.new(
        hmac_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{timestamp}:{username}:{signature}"


def validate_session_token(token: str, hmac_key: str) -> str | None:
    """Validate session token. Returns username or None."""
    # Format is timestamp:username:signature. Peel the timestamp off the front
    # and the signature off the back so usernames containing ":" (media-server
    # accounts we don't control) still round-trip.
    try:
        timestamp_str, rest = token.split(":", 1)
        username, signature = rest.rsplit(":", 1)
    except ValueError:
        return None

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return None

    # Check token age
    if time.time() - timestamp > SESSION_MAX_AGE:
        return None

    # Verify HMAC signature
    payload = f"{timestamp_str}:{username}"
    expected = hmac.new(
        hmac_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    if not secrets.compare_digest(signature, expected):
        return None

    return username


# ── Cookie helpers ──────────────────────────────────────────────────

COOKIE_NAME = "hf_session"

TRUSTED_PROXIES: frozenset[str] = frozenset(
    p.strip() for p in os.environ.get("HF_TRUSTED_PROXIES", "").split(",") if p.strip()
)


def peer_is_trusted_proxy(request: Request) -> bool:
    """True only when the direct TCP peer is listed in HF_TRUSTED_PROXIES."""
    return bool(request.client) and request.client.host in TRUSTED_PROXIES


def is_https(request: Request) -> bool:
    """Check if request arrived over HTTPS.

    X-Forwarded-Proto is honoured only from a trusted proxy; any other
    peer could set it to strip the Secure flag off the session cookie."""
    if request.url.scheme == "https":
        return True
    if peer_is_trusted_proxy(request):
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        return forwarded_proto.lower() == "https"
    return False


def set_session_cookie(response, token: str, request: Request) -> None:
    """Set the session cookie with appropriate security flags."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=is_https(request),
        max_age=SESSION_MAX_AGE,
        path="/",
    )


def clear_session_cookie(response, request: Request) -> None:
    """Clear the session cookie (attributes must match how it was set)."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_https(request),
    )


# ── Auth middleware ──────────────────────────────────────────────────

# Exact paths that never require auth
UNAUTH_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/setup/status",
}


def _is_unauth_allowed(path: str) -> bool:
    """Check if this path is allowed without authentication."""
    # Static files and SPA root
    if path == "/" or path.startswith("/static/"):
        return True

    # Exact match against allowlist
    if path in UNAUTH_PATHS:
        return True

    # TTS audio served to browser via new Audio(url)
    if path.startswith("/api/tts/audio/"):
        return True

    return False


def _is_setup_path(path: str) -> bool:
    """Check if this is a setup endpoint."""
    return path.startswith("/api/setup/") and path != "/api/setup/status"


class AuthMiddleware(BaseHTTPMiddleware):
    def _session_user(self, request: Request) -> dict | None:
        """Resolve the session cookie to an active user row, or None.

        None covers every failure alike: no cookie, no signing key, a bad or
        expired token, a deleted user, or a disabled account."""
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        hmac_key = get_setting("hmac_key")
        if not hmac_key:
            return None
        username = validate_session_token(token, hmac_key)
        if not username:
            return None
        user = get_user_by_username(username)
        if not user or user.get("disabled"):
            return None
        return user

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow unauthenticated paths
        if _is_unauth_allowed(path):
            return await call_next(request)

        # Setup paths: only allowed when setup is not complete. Once an admin
        # account exists, steps 2-5 require that admin's session so a second
        # caller on the LAN cannot finish (or redirect) someone else's setup.
        # Step 1 stays open because it carries its own current_password
        # claim-lock, and it is where a signed-out admin re-authenticates.
        if _is_setup_path(path):
            if is_setup_complete():
                return JSONResponse({"error": "Setup is already complete"}, status_code=403)
            admin = get_local_admin()
            if admin and admin.get("password_hash") and path != "/api/setup/auth":
                user = self._session_user(request)
                if not user or user.get("role") != "admin":
                    return JSONResponse(
                        {"error": "Sign in with the admin account to continue setup"},
                        status_code=401,
                    )
                request.state.user = user
                request.state.username = user["username"]
            return await call_next(request)

        # Everything else requires a valid session
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return JSONResponse(
                {"error": "Authentication required"}, status_code=401
            )

        hmac_key = get_setting("hmac_key")
        if not hmac_key:
            return JSONResponse(
                {"error": "Authentication required"}, status_code=401
            )

        username = validate_session_token(token, hmac_key)
        if not username:
            return JSONResponse(
                {"error": "Invalid or expired session"}, status_code=401
            )

        # Resolve the full user row — deleting a user revokes their sessions
        user = get_user_by_username(username)
        if not user:
            return JSONResponse(
                {"error": "Invalid or expired session"}, status_code=401
            )
        if user.get("disabled"):
            return JSONResponse(
                {"error": "Account disabled"}, status_code=401
            )

        request.state.username = user["username"]
        request.state.user = user
        return await call_next(request)
