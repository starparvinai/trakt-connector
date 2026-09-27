# Trakt Connector — M1 Auth Spine

M1 builds the OAuth2 auth spine: Trakt app credentials, authorize/callback
endpoints, encrypted per-user token storage, and single-use refresh rotation.

## Setup (local prototype)

1. Create a Trakt API app and set the redirect URI to your callback URL.
   Keep the **client secret out of chat** — it only lives in env vars.
2. Export env vars:
   ```bash
   export TRAKT_CLIENT_ID="..."
   export TRAKT_CLIENT_SECRET="..."
   export TRAKT_REDIRECT_URI="http://localhost:8000/oauth/callback"
   export TOKEN_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
   ```
3. Run:
   ```bash
   .venv/bin/uvicorn app.main:app --reload --port 8000
   ```
4. Visit `http://localhost:8000/oauth/start?user_id=demo` to authorize.

## Security notes

- Client secret never leaves the server process; never logged.
- Tokens encrypted at rest with Fernet (`TOKEN_ENCRYPTION_KEY`).
- Refresh tokens are single-use: every refresh atomically replaces the stored pair.
- `invalid_grant` / `session not found` deletes stored tokens and asks for re-auth.
