-- ============================================================================
-- nyaya-memory-chat : authentication (email/password + OAuth) in `memchat`.
-- Isolated from the read-only public judgment tables, like the rest of memchat.
-- ============================================================================

-- One row per real account. `password_hash` is null for OAuth-only accounts.
CREATE TABLE IF NOT EXISTS memchat.users (
    id             TEXT PRIMARY KEY,                       -- u_<hex>
    email          TEXT UNIQUE NOT NULL,
    name           TEXT,
    password_hash  TEXT,                                   -- pbkdf2_sha256$...  (null for oauth-only)
    provider       TEXT NOT NULL DEFAULT 'password',       -- 'password' | 'google'
    provider_sub   TEXT,                                   -- OAuth subject id
    avatar_url     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_provider_sub
    ON memchat.users (provider, provider_sub)
    WHERE provider_sub IS NOT NULL;

-- Server-side sessions. We store only the SHA-256 of the opaque cookie token,
-- so a DB read never leaks a usable session secret.
CREATE TABLE IF NOT EXISTS memchat.auth_sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES memchat.users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON memchat.auth_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_exp  ON memchat.auth_sessions (expires_at);
