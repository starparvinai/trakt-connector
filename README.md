# Trakt Connector

OAuth2 auth spine plus agent-oriented read/write tools for [Trakt.tv](https://trakt.tv) —
search, history, watchlist, collection, ratings, stats, progress calendars, and logging
watches. Built as the adapter layer a personal AI agent would call: every tool returns
flat, predictable schemas designed for LLM callers, and every destructive operation
goes through a machine-readable confirmation gate.

## Architecture

```
Agent / CLI
    │  calls a tool (search, log_episode, rate_movie, …)
    ▼
app/read_tools.py, app/write_tools.py   ← flat, normalized schemas
    │  TraktClient: auth headers, refresh, retries
    ▼
app/token_store.py                       ← Fernet-encrypted, per-user
    │
    ▼
Trakt API (api.trakt.tv)                 ← OAuth2 bearer + api-key headers
```

`app/main.py` exposes the OAuth authorize/callback endpoints (FastAPI).
`scripts/trakt_cli.py` is a thin CLI over the same tools — useful for trying
everything without an agent in the loop.

## Milestones

**M1 — Auth spine.** Full OAuth2 Authorization Code flow: `/oauth/start` builds the
Trakt authorize URL, `/oauth/callback` exchanges the code and stores the token pair
encrypted at rest (Fernet, per-user keys in SQLite). `TraktClient` injects the
`trakt-api-key` / `trakt-api-version` / bearer headers on every call.

**M2 — Read tools** (`app/read_tools.py`). Search, trending, popular,
recommendations, watch history (date-filtered), watchlist, collection, ratings,
next-episode progress, upcoming calendar, and user stats. The key move is
**normalization**: Trakt nests payloads differently per endpoint
(search wraps media under a type key, history buries episodes inside show objects),
so every tool returns the same flat shape — `title`, `year`, `ids`, plus the
tool-specific field. An LLM caller never guesses where the title lives.

**M3 — Write tools** (`app/write_tools.py`). Log movies/episodes, rate
movies/shows/episodes, add/remove watchlist entries, remove history entries.
Destructive operations use a **confirmation gate as a data structure**: called
without `confirm=True` they return a `needs_confirmation` preview of exactly what
would be deleted and perform zero HTTP calls. The agent shows the preview, the user
approves, the agent re-calls with `confirm=True`.

**M4 — Production hardening** (`app/state.py`, `app/auth.py`, `app/tools_registry.py`).
Three changes that make the adapter multi-user-ready:
**signed, expiring OAuth state** (itsdangerous) replaces the in-memory
`_state_to_user` dict — stateless, works across restarts and workers, forged or
expired tokens are rejected; **API-key identity** replaces the trusted
`user_id` query param — `/oauth/callback` mints a random key (stored as a
SHA-256 hash, shown once) and `/tools/*` requires it as `X-API-Key`;
**per-user refresh locking** (double-checked, process-wide) so two concurrent
requests can't double-fire Trakt's single-use refresh and corrupt the pair.
Plus a single dispatch surface: `POST /tools/invoke {"tool", "arguments"}`
mirrors MCP's `tools/call` shape, making a future MCP front a thin port.

## Design decisions

- **Single-use refresh rotation.** Trakt refresh tokens are one-time-use; every
  refresh atomically replaces the stored pair, so a leaked or replayed refresh
  token is worthless.
- **401 → refresh → retry.** One automatic retry per request; `invalid_grant`
  purges tokens and forces re-authorization instead of retrying forever.
- **429 backoff, 426 honesty.** Rate limits get exponential backoff with
  `Retry-After`; a 426 (account upgrade required) surfaces as a clear error
  rather than a retry loop.
- **Client-side date filtering, bounded pagination** on history (100/page, 5 pages
  max) — no runaway API walks.
- **Episode resolution.** Write tools accept human-friendly `show/season/number`
  and resolve the episode's Trakt ID via the season listing, so callers never
  handle raw episode IDs.

## Setup (local prototype)

1. Create a Trakt API app and set the redirect URI to your callback URL.
   Keep the **client secret out of chat** — it only lives in env vars.
2. Export env vars (or put them in a `.env` file and `set -a; source .env; set +a`):

   ```bash
   export TRAKT_CLIENT_ID="..."
   export TRAKT_CLIENT_SECRET="..."
   export TRAKT_REDIRECT_URI="http://localhost:8000/oauth/callback"
   export TRAKT_USER_ID="parvin"
   export TOKEN_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
   # Optional: dedicated secret for signing OAuth state; defaults to the key above.
   export OAUTH_STATE_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
   ```

   The encryption key must be stable — regenerating it orphans stored tokens.
3. Install and run:

   ```bash
   python -m venv .venv && .venv/bin/pip install -q fastapi uvicorn httpx cryptography pydantic itsdangerous
   .venv/bin/uvicorn app.main:app --port 8000
   ```

4. Authorize: visit `http://localhost:8000/oauth/start`, approve on Trakt.
   The callback stores your tokens and returns an **API key shown once** — save
   it; every `/tools/*` call needs it as the `X-API-Key` header.
   Confirm the auth spine with `.venv/bin/python scripts/verify_auth.py`.
5. List and call tools over HTTP:

   ```bash
   curl -H "X-API-Key: $CONNECTOR_API_KEY" http://localhost:8000/tools/list
   curl -X POST -H "X-API-Key: $CONNECTOR_API_KEY" -H "Content-Type: application/json" \
     -d '{"tool": "stats", "arguments": {}}' http://localhost:8000/tools/invoke
   ```

> **Upgrading from M1–M3?** M4 changed the identity model: `/oauth/start` no
> longer takes `user_id`, and tokens are keyed by a generated connector user id.
> Re-authorize once to mint your API key. The CLI (`scripts/trakt_cli.py`) still
> works directly with `TRAKT_USER_ID` for local dev.
5. Try the tools:

   ```bash
   .venv/bin/python scripts/trakt_cli.py stats
   .venv/bin/python scripts/trakt_cli.py search "dune" --type movie
   .venv/bin/python scripts/trakt_cli.py log-movie dune-2021
   .venv/bin/python scripts/trakt_cli.py rate-movie dune-2021 9
   .venv/bin/python scripts/trakt_cli.py watchlist-add movies dune-part-two-2024
   .venv/bin/python scripts/trakt_cli.py next breaking-bad
   ```

## Testing

```bash
.venv/bin/python -m pytest -q
```

24 tests: encrypted token roundtrips, refresh-pair replacement, 401 refresh/retry,
`invalid_grant` purge, read-tool normalization (search/history/stats/watchlist),
write payloads, episode-ID resolution, confirmation gates asserting **zero**
HTTP calls fire before `confirm=True`, plus M4: signed-state roundtrip/tamper,
429 backoff (3 attempts then success), 426 VIP surfacing, concurrent-refresh
single-flight, API-key mint/resolve/reject, `/oauth/start` redirect,
`/oauth/callback` key issuance, and the `/tools/invoke` auth gate + dispatch.

## Security notes

- Client secret never leaves the server process; never logged.
- Tokens encrypted at rest with Fernet; the key lives only in the environment.
- `.env`, `*.db*`, and `.venv` are gitignored — no credentials in the repo.
- Destructive tools cannot execute without explicit confirmation.

## Roadmap

- **M4 — Production hardening.** ✅ Done: signed expiring OAuth state, API-key
  per-user auth, per-user refresh locking, single `/tools/invoke` dispatch.
  Remaining for real production: Postgres over SQLite, secrets in a manager,
  distributed refresh lock (Redis) for multi-worker deploys.
- **M5 — Multi-user service.** Hosted adapter with public callback URL, per-user
  token isolation, per-user rate limiting and audit logging, MCP/REST front so any
  agent host can list and call the tools.
