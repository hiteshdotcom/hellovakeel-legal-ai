"""Authentication: email/password + Google OAuth, with DB-backed sessions.

Design
------
* Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib, no extra deps), stored
  as ``pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>``.
* Sessions are opaque random tokens set in an HttpOnly cookie. Only the
  SHA-256 of the token is stored (``memchat.auth_sessions``), so the DB never
  holds a usable credential. Sessions are server-side => revocable on logout.
* Google OAuth is the Authorization-Code flow with a CSRF ``state`` value kept
  in a short-lived HttpOnly cookie (double-submit; no shared secret needed).
  It activates automatically when GOOGLE_CLIENT_ID/SECRET are configured.

All user identity for the rest of the app is derived from the session cookie
(:func:`resolve_user_id`) — never trusted from the request body.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import jwt
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

from .config import get_settings
from .db import get_db

logger = logging.getLogger("nyaya.auth")
router = APIRouter()

SESSION_COOKIE = "nyaya_session"
OAUTH_STATE_COOKIE = "nyaya_oauth_state"
_PBKDF2_ITERS = 200_000

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


# --------------------------------------------------------------------------- #
#  password hashing (stdlib PBKDF2)
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: Optional[str]) -> bool:
    if not encoded:
        return False
    try:
        algo, iters_s, salt_hex, hash_hex = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters_s)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  user + session persistence (memchat.users / memchat.auth_sessions)
# --------------------------------------------------------------------------- #
def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    """Only fields safe to hand back to the client — never the password hash."""
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row.get("name") or (row["email"].split("@")[0] if row.get("email") else ""),
        "avatar_url": row.get("avatar_url"),
        "provider": row.get("provider") or "password",
    }


async def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    db = get_db()
    if not db.pool:
        return None
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM memchat.users WHERE lower(email) = lower($1)", email
        )
    return dict(row) if row else None


async def get_user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    db = get_db()
    if not db.pool:
        return None
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM memchat.users WHERE id = $1", user_id)
    return dict(row) if row else None


async def create_user(
    *,
    email: str,
    name: str = "",
    password_hash: Optional[str] = None,
    provider: str = "password",
    provider_sub: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> dict[str, Any]:
    db = get_db()
    uid = "u_" + uuid.uuid4().hex[:16]
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memchat.users
                (id, email, name, password_hash, provider, provider_sub, avatar_url, last_login_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, now())
            RETURNING *
            """,
            uid, email, name or None, password_hash, provider, provider_sub, avatar_url,
        )
    return dict(row)


async def touch_login(user_id: str) -> None:
    db = get_db()
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE memchat.users SET last_login_at = now() WHERE id = $1", user_id)


async def create_session(user_id: str, user_agent: str = "") -> str:
    """Issue an opaque token; store only its hash. Returns the raw token."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = _now() + timedelta(days=get_settings().AUTH_SESSION_DAYS)
    db = get_db()
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memchat.auth_sessions (token_hash, user_id, expires_at, user_agent)
            VALUES ($1, $2, $3, $4)
            """,
            token_hash, user_id, expires, (user_agent or "")[:400],
        )
    return token


async def delete_session(token: str) -> None:
    if not token:
        return
    db = get_db()
    if not db.pool:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    async with db.pool.acquire() as conn:
        await conn.execute("DELETE FROM memchat.auth_sessions WHERE token_hash = $1", token_hash)


async def _session_user(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    db = get_db()
    if not db.pool:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT u.* FROM memchat.auth_sessions s
            JOIN memchat.users u ON u.id = s.user_id
            WHERE s.token_hash = $1 AND s.expires_at > now()
            """,
            token_hash,
        )
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
#  request helpers used by other routers
# --------------------------------------------------------------------------- #
async def current_user(request: Request) -> Optional[dict[str, Any]]:
    return await _session_user(request.cookies.get(SESSION_COOKIE, ""))


async def resolve_user_id(request: Request) -> Optional[str]:
    """The authoritative user_id for this request, from the session cookie.
    Returns None when unauthenticated — callers should reject with 401."""
    user = await current_user(request)
    return user["id"] if user else None


def _secure_cookie(request: Request) -> bool:
    # Secure flag breaks cookies over plain-http localhost; enable only on https.
    return request.url.scheme == "https"


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().AUTH_SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(request),
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    # Deletion must mirror the attributes used at set time (path/samesite/secure/
    # httponly); a mismatched Set-Cookie can be ignored by the browser, leaving a
    # stale cookie that re-authenticates on refresh.
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(request),
    )


def _redirect_base(request: Request) -> str:
    base = get_settings().OAUTH_REDIRECT_BASE.strip()
    if base:
        return base.rstrip("/")
    return str(request.base_url).rstrip("/")


# --------------------------------------------------------------------------- #
#  request/response models
# --------------------------------------------------------------------------- #
class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)
    name: str = ""

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("Please enter a valid email address.")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=200)


# --------------------------------------------------------------------------- #
#  routes
# --------------------------------------------------------------------------- #
@router.get("/providers")
async def providers():
    s = get_settings()
    return {
        "password": True,
        "google": s.google_oauth_enabled,
        "clerk": s.clerk_enabled,
        # publishable key + frontend API are public — the browser needs them to load Clerk.
        "clerk_publishable_key": s.CLERK_PUBLISHABLE_KEY if s.clerk_enabled else "",
        "clerk_frontend_api": s.clerk_frontend_api if s.clerk_enabled else "",
    }


@router.get("/me")
async def me(request: Request):
    user = await current_user(request)
    if not user:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return {"user": _public_user(user)}


@router.post("/signup")
async def signup(req: SignupRequest, request: Request):
    db = get_db()
    if not db.pool:
        return JSONResponse({"error": "database unavailable"}, status_code=503)
    email = req.email.strip().lower()
    if await get_user_by_email(email):
        return JSONResponse({"error": "An account with this email already exists."}, status_code=409)
    user = await create_user(
        email=email, name=req.name.strip(), password_hash=hash_password(req.password),
        provider="password",
    )
    token = await create_session(user["id"], request.headers.get("user-agent", ""))
    resp = JSONResponse({"user": _public_user(user)})
    _set_session_cookie(resp, token, request)
    return resp


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    db = get_db()
    if not db.pool:
        return JSONResponse({"error": "database unavailable"}, status_code=503)
    user = await get_user_by_email(req.email.strip().lower())
    if not user or not verify_password(req.password, user.get("password_hash")):
        # Uniform message: don't reveal whether the email exists.
        return JSONResponse({"error": "Invalid email or password."}, status_code=401)
    await touch_login(user["id"])
    token = await create_session(user["id"], request.headers.get("user-agent", ""))
    resp = JSONResponse({"user": _public_user(user)})
    _set_session_cookie(resp, token, request)
    return resp


@router.post("/logout")
async def logout(request: Request):
    await delete_session(request.cookies.get(SESSION_COOKIE, ""))
    resp = JSONResponse({"ok": True})
    _clear_session_cookie(resp, request)
    return resp


# ---- Google OAuth (Authorization Code flow) ----
@router.get("/google/login")
async def google_login(request: Request):
    s = get_settings()
    if not s.google_oauth_enabled:
        return JSONResponse(
            {"error": "Google sign-in is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."},
            status_code=503,
        )
    state = secrets.token_urlsafe(24)
    redirect_uri = _redirect_base(request) + "/api/auth/google/callback"
    params = {
        "client_id": s.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = GOOGLE_AUTH_URL + "?" + httpx.QueryParams(params).__str__()
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        OAUTH_STATE_COOKIE, state, max_age=600, httponly=True,
        samesite="lax", secure=_secure_cookie(request), path="/",
    )
    return resp


@router.get("/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    s = get_settings()
    if error:
        return _oauth_error("Google sign-in was cancelled.")
    if not s.google_oauth_enabled:
        return _oauth_error("Google sign-in is not configured.")
    expected = request.cookies.get(OAUTH_STATE_COOKIE, "")
    if not state or not expected or not hmac.compare_digest(state, expected):
        return _oauth_error("Sign-in state check failed. Please try again.")
    if not code:
        return _oauth_error("Missing authorization code.")

    redirect_uri = _redirect_base(request) + "/api/auth/google/callback"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            tok = await client.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": s.GOOGLE_CLIENT_ID,
                "client_secret": s.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            tok.raise_for_status()
            access_token = tok.json().get("access_token")
            if not access_token:
                return _oauth_error("Google did not return an access token.")
            ui = await client.get(
                GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            ui.raise_for_status()
            info = ui.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Google OAuth exchange failed: %s", exc)
        return _oauth_error("Could not complete Google sign-in.")

    sub = info.get("sub")
    email = (info.get("email") or "").strip().lower()
    if not sub or not email:
        return _oauth_error("Google account did not provide an email.")
    name = info.get("name") or ""
    picture = info.get("picture")

    # Link by provider_sub, else by email, else create.
    db = get_db()
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM memchat.users WHERE provider = 'google' AND provider_sub = $1", sub
        )
    user = dict(row) if row else await get_user_by_email(email)
    if user:
        async with db.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE memchat.users
                SET provider = 'google', provider_sub = $2,
                    avatar_url = COALESCE($3, avatar_url),
                    name = COALESCE(NULLIF($4,''), name),
                    last_login_at = now()
                WHERE id = $1
                """,
                user["id"], sub, picture, name,
            )
    else:
        user = await create_user(
            email=email, name=name, provider="google", provider_sub=sub, avatar_url=picture,
        )

    token = await create_session(user["id"], request.headers.get("user-agent", ""))
    resp = RedirectResponse("/", status_code=302)
    _set_session_cookie(resp, token, request)
    resp.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return resp


def _oauth_error(message: str) -> RedirectResponse:
    """Bounce back to the sign-in page with an error the SPA can surface."""
    from urllib.parse import quote

    return RedirectResponse(f"/?auth_error={quote(message)}", status_code=302)


# --------------------------------------------------------------------------- #
#  Clerk: verify a Clerk session token, then mint OUR session cookie.
#
#  Clerk owns the Google sign-in UX on the frontend. Here we (1) verify the
#  short-lived Clerk session JWT against Clerk's JWKS (RS256, networkless after
#  the keys are cached), and (2) read the authoritative profile from Clerk's
#  Backend API with the secret key. The verified `sub` is the account identity;
#  everything downstream keeps using our own revocable session cookie.
# --------------------------------------------------------------------------- #
_clerk_jwk_client: Optional["jwt.PyJWKClient"] = None


def _clerk_jwks() -> "jwt.PyJWKClient":
    global _clerk_jwk_client
    if _clerk_jwk_client is None:
        issuer = get_settings().clerk_issuer
        _clerk_jwk_client = jwt.PyJWKClient(issuer + "/.well-known/jwks.json")
    return _clerk_jwk_client


def verify_clerk_token(token: str) -> dict[str, Any]:
    """Verify a Clerk session JWT and return its claims (raises on failure)."""
    s = get_settings()
    signing_key = _clerk_jwks().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=s.clerk_issuer,
        options={"verify_aud": False, "require": ["exp", "iat", "sub"]},
        leeway=10,
    )
    return claims


async def _clerk_get_user(clerk_user_id: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{s.CLERK_API_BASE}/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {s.CLERK_SECRET_KEY}"},
        )
        r.raise_for_status()
        return r.json()


def _clerk_primary_email(cu: dict[str, Any]) -> str:
    addrs = cu.get("email_addresses") or []
    primary_id = cu.get("primary_email_address_id")
    for a in addrs:
        if a.get("id") == primary_id and a.get("email_address"):
            return a["email_address"].strip().lower()
    for a in addrs:
        if a.get("email_address"):
            return a["email_address"].strip().lower()
    return ""


async def _upsert_clerk_user(
    sub: str, email: str, name: str, avatar: Optional[str]
) -> dict[str, Any]:
    db = get_db()
    # Prefer an existing Clerk-linked row; else link an existing account by email.
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM memchat.users WHERE provider = 'clerk' AND provider_sub = $1", sub
        )
    user = dict(row) if row else (await get_user_by_email(email) if email else None)
    if user:
        async with db.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE memchat.users
                SET provider = 'clerk', provider_sub = $2,
                    avatar_url = COALESCE($3, avatar_url),
                    name = COALESCE(NULLIF($4, ''), name),
                    last_login_at = now()
                WHERE id = $1
                """,
                user["id"], sub, avatar, name,
            )
        return await get_user_by_id(user["id"]) or user
    return await create_user(
        email=email or f"{sub}@users.clerk", name=name,
        provider="clerk", provider_sub=sub, avatar_url=avatar,
    )


class ClerkExchange(BaseModel):
    token: str


@router.post("/clerk")
async def clerk_exchange(req: ClerkExchange, request: Request):
    """Exchange a verified Clerk session token for our own session cookie."""
    s = get_settings()
    if not s.clerk_enabled:
        return JSONResponse({"error": "Clerk is not configured."}, status_code=503)
    if not get_db().pool:
        return JSONResponse({"error": "database unavailable"}, status_code=503)
    try:
        claims = verify_clerk_token(req.token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clerk token verification failed: %s", exc)
        return JSONResponse({"error": "Invalid or expired Clerk session."}, status_code=401)
    sub = claims.get("sub")
    if not sub:
        return JSONResponse({"error": "Clerk token missing subject."}, status_code=401)
    try:
        cu = await _clerk_get_user(sub)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clerk user fetch failed: %s", exc)
        return JSONResponse({"error": "Could not load your Clerk profile."}, status_code=502)

    email = _clerk_primary_email(cu)
    name = " ".join(x for x in [cu.get("first_name"), cu.get("last_name")] if x).strip()
    avatar = cu.get("image_url")
    user = await _upsert_clerk_user(sub, email, name, avatar)

    token = await create_session(user["id"], request.headers.get("user-agent", ""))
    resp = JSONResponse({"user": _public_user(user)})
    _set_session_cookie(resp, token, request)
    return resp
