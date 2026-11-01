import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..auth import (
    COOKIE_NAME,
    PBKDF2_ITERATIONS,
    clear_session_cookie,
    create_session_token,
    hash_password,
    peer_is_trusted_proxy,
    set_session_cookie,
    validate_session_token,
    verify_user_password,
)
from ..config import (
    create_user,
    get_media_servers,
    get_setting,
    get_user_by_username,
    set_user_disabled,
    touch_user_login,
    update_user_password,
    upsert_identity,
)
from ..models import AuthStatus, LoginRequest

logger = logging.getLogger("hailfrequency.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Login throttling (in-process; container runs a single worker) ──
MAX_FAILURES = 5
LOCKOUT_SECONDS = 30
USER_DELAY_AFTER = 3
USER_DELAY_MAX_SECONDS = 8
# Keyed on both the client IP ("ip:...") and the attempted username
# ("user:..."). Only the IP key locks: MAX_FAILURES from one address earn a
# LOCKOUT_SECONDS lockout, and further failures while locked do not extend
# it. The username key never locks — it only adds a growing delay to every
# attempt on that name — so a hostile client cannot lock the household out
# of its own accounts while a distributed guess against one account is still
# slowed. count is NOT reset on lockout — repeat offenders stay in the slow
# lane until a success clears them.
_login_failures: dict[str, tuple[int, float]] = {}  # key -> (count, locked_until)


def _client_ip(request: Request) -> str:
    """Real client IP. X-Forwarded-For is honoured only when the direct peer
    is listed in HF_TRUSTED_PROXIES; such a proxy appends the true client to
    the header, so the RIGHTMOST entry is the one it set (a spoofed leftmost
    value can't displace it). From any other peer the header is ignored and
    the direct peer address is used."""
    if peer_is_trusted_proxy(request):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1]
    return request.client.host if request.client else "unknown"


def _is_locked_out(*keys: str) -> bool:
    now = time.time()
    return any(now < _login_failures.get(k, (0, 0.0))[1] for k in keys)


def _record_failure(*keys: str) -> None:
    # Keep the table bounded — drop expired entries if it grows
    if len(_login_failures) > 1000:
        now = time.time()
        for key in [k for k, (_, t) in _login_failures.items() if t < now]:
            del _login_failures[key]
    for k in keys:
        count, locked_until = _login_failures.get(k, (0, 0.0))
        if time.time() < locked_until:
            # Already locked: a failure during the lockout must not push
            # the release time further out
            continue
        count += 1
        if k.startswith("ip:") and count >= MAX_FAILURES:
            _login_failures[k] = (count, time.time() + LOCKOUT_SECONDS)
            logger.warning("Login lockout for %s after %d failures", k, count)
        else:
            _login_failures[k] = (count, 0.0)


def _record_success(*keys: str) -> None:
    for k in keys:
        _login_failures.pop(k, None)


async def _delegated_login(username: str, password: str) -> list[tuple]:
    """Try AuthenticateByName against every Emby/Jellyfin server.

    Returns [(server, media_user_id, media_username, access_token), ...] —
    one entry per server that accepted the credentials. Same one call works
    on both (Jellyfin kept Emby's auth endpoint).
    """
    auth_header = (
        'MediaBrowser Client="HailFrequency", Device="HailFrequency", '
        f'DeviceId="hf-user-{username.lower()}", Version="1.0"'
    )
    targets = [
        s for s in get_media_servers() if s["type"] in ("emby", "jellyfin")
    ]

    async def _probe(client, server):
        prefix = "/emby" if server["type"] == "emby" else ""
        try:
            resp = await client.post(
                f"{server['url']}{prefix}/Users/AuthenticateByName",
                json={"Username": username, "Pw": password},
                headers={
                    "X-Emby-Authorization": auth_header,
                    "Authorization": auth_header,
                },
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            media_user = data.get("User", {})
            # Without an access token the identity could only ever act on
            # the server's own credentials, so it is not an acceptance.
            if media_user.get("Id") and data.get("AccessToken"):
                return (
                    server,
                    media_user["Id"],
                    media_user.get("Name", username),
                    data["AccessToken"],
                )
        except Exception:
            logger.debug(
                "Delegated login unreachable: %s", server["name"], exc_info=True
            )
        return None

    # Probe every server concurrently so one unreachable server doesn't add
    # its full 10s timeout to the login attempt
    async with httpx.AsyncClient(timeout=10.0) as client:
        probed = await asyncio.gather(*(_probe(client, s) for s in targets))
    return [r for r in probed if r is not None]


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    ip_key = f"ip:{_client_ip(request)}"
    user_key = f"user:{body.username.strip().lower()}"
    if _is_locked_out(ip_key):
        return JSONResponse(
            {"error": "Too many failed attempts. Try again in a moment."},
            status_code=429,
        )

    # Per-username slowdown: every attempt on a name with repeated failures
    # waits 1, 2, 4, 8, 8… seconds before any credential check. This slows a
    # distributed guess against one account without ever locking it.
    count = _login_failures.get(user_key, (0, 0.0))[0]
    if count >= USER_DELAY_AFTER:
        await asyncio.sleep(
            min(2 ** (count - USER_DELAY_AFTER), USER_DELAY_MAX_SECONDS)
        )

    hmac_key = get_setting("hmac_key")
    if not hmac_key:
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)

    user = get_user_by_username(body.username)

    # A disabled account is only reported AFTER the credentials check out:
    # answering "disabled" to a wrong password would confirm the account
    # exists. Valid credentials on a disabled account are not a throttle
    # failure, and never earn a cookie.
    disabled = JSONResponse({"error": "Account disabled"}, status_code=403)

    # 1. Local account (the admin) — password stored in the users table
    if user and user.get("password_hash"):
        if not verify_user_password(user, body.password):
            _record_failure(ip_key, user_key)
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)
        if user.get("disabled"):
            return disabled
        # Transparent hash migration; hmac_key untouched so sessions survive
        iterations = user.get("password_iterations") or 100_000
        if iterations < PBKDF2_ITERATIONS:
            new_hash, new_salt = hash_password(body.password)
            update_user_password(user["id"], new_hash, new_salt, PBKDF2_ITERATIONS)
            logger.info("Password hash migrated to %d iterations", PBKDF2_ITERATIONS)

    # 2. Delegated: sign in with a media-server account (Emby/Jellyfin)
    else:
        matches = await _delegated_login(body.username, body.password)
        if not matches:
            _record_failure(ip_key, user_key)
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)
        is_new = user is None
        if not is_new and user.get("disabled"):
            return disabled
        if is_new:
            user = create_user(username=body.username, role="user")
            logger.info(
                "New user '%s' via %s", user["username"], matches[0][0]["name"]
            )
        # Link an identity for EVERY server that accepted these credentials
        for server, media_uid, media_name, token in matches:
            upsert_identity(user["id"], server["id"], media_uid, media_name, token)

        # Default-deny: new sign-ins are parked disabled until an admin
        # enables them (pre-enabled/provisioned accounts pass straight through)
        if is_new:
            set_user_disabled(user["id"], True)
            _record_success(ip_key, user_key)  # valid creds — not a failure
            logger.info("User '%s' awaiting admin approval", user["username"])
            return JSONResponse(
                {"error": "Account created — awaiting admin approval."},
                status_code=403,
            )

    _record_success(ip_key, user_key)
    touch_user_login(user["id"])

    session = create_session_token(user["username"], hmac_key)
    response = JSONResponse(
        AuthStatus(
            authenticated=True, username=user["username"], role=user["role"]
        ).model_dump()
    )
    set_session_cookie(response, session, request)
    return response


@router.post("/logout")
async def logout(request: Request):
    response = JSONResponse(AuthStatus(authenticated=False).model_dump())
    clear_session_cookie(response, request)
    return response


@router.get("/status")
async def status(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return AuthStatus(authenticated=False)

    hmac_key = get_setting("hmac_key")
    if not hmac_key:
        return AuthStatus(authenticated=False)

    username = validate_session_token(token, hmac_key)
    if not username:
        return AuthStatus(authenticated=False)

    user = get_user_by_username(username)
    if not user or user.get("disabled"):
        return AuthStatus(authenticated=False)

    return AuthStatus(
        authenticated=True, username=user["username"], role=user["role"]
    )
