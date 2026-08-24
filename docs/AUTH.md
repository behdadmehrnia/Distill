# Auth and database

Distill stores **users and meetings** in **PostgreSQL**. The browser UI under `api/web/` uses email/password login and a JWT cookie. OAuth/SSO routes exist as stubs (HTTP 501) for later work.

Related files: `.env.example`, `api/config.py`, `api/auth/`, `api/db.py`, `docker-compose.yml` (`postgres` service).

---

## Quick start

```bash
# 1) Postgres (Docker)
docker compose up -d postgres

# 2) Root .env (copy from example if needed)
cp -n .env.example .env
# Set at least JWT_SECRET and DATABASE_URL (defaults work for local Docker Postgres)

# 3) API
pip install -r requirements.txt
python -m api
```

Open:

| URL | Purpose |
|-----|---------|
| `/register` | Create account |
| `/login` | Sign in |
| `/dashboard` | Your meetings (requires login) |
| `/assistant/{id}` | Meeting workspace (owner only) |

---

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://distill:distill@127.0.0.1:5432/distill` | Postgres connection string (users + meetings) |
| `JWT_SECRET` | long insecure placeholder | HS256 signing key — **change in production** (use a long random string) |
| `JWT_EXPIRE_MINUTES` | `10080` (7 days) | Cookie / token lifetime |
| `AUTH_COOKIE_SECURE` | `0` | Set `1` when the app is served over HTTPS so the cookie is `Secure` |

Paths under `data/` (uploads, audio, STT cache) stay on disk; they are **not** in Postgres. The old SQLite file `data/meetings.db` is unused after the Postgres migration and is not read by the app.

---

## PostgreSQL

### What is stored

Tables are created automatically on API startup (`CREATE TABLE IF NOT EXISTS`):

- **users** — id, email (unique), password hash (bcrypt), display name, created_at, is_active
- **meetings** — metadata including `user_id` (owner)
- **segments**, **insights**, **speaker_intervals**, **minutes** — meeting content

### Docker Compose

Root `docker-compose.yml` defines a `postgres:16-alpine` service:

- User / password / database: `distill` / `distill` / `distill`
- Host port: `127.0.0.1:5432`
- Volume: `distill-postgres`

When the **API** also runs in Compose, it gets:

```text
DATABASE_URL=postgresql://distill:distill@postgres:5432/distill
```

When the API runs on the host and Postgres is in Docker, use localhost:

```text
DATABASE_URL=postgresql://distill:distill@127.0.0.1:5432/distill
```

### Local Postgres without Docker

Create a role and database that match `DATABASE_URL`, for example:

```bash
createuser -h 127.0.0.1 -P distill   # password: distill
createdb  -h 127.0.0.1 -O distill distill
```

Grant `CREATE` on the database if tests need isolated schemas (pytest creates temporary schemas).

### Migration from SQLite

There is **no automatic import** from `data/meetings.db`. Register again (or insert users manually) and create new meetings. Old SQLite rows are left on disk only.

### Tests

Pytest expects a reachable Postgres at `DATABASE_URL` (or the default above). Each test uses a temporary schema (`search_path`) and drops it afterward. Start Postgres before running tests:

```bash
docker compose up -d postgres   # or your local instance
pytest
```

---

## Auth model

### Email / password

- **Register:** `POST /auth/register` — JSON `{ "email", "password", "display_name"? }`
  - Email normalized to lowercase; basic format check
  - Password minimum length: **8**
  - Conflict: `409` if email already exists
  - On success: creates user, sets cookie, returns `{ "user": ... }`
- **Login:** `POST /auth/login` — JSON `{ "email", "password" }` → cookie + user
- **Logout:** `POST /auth/logout` — clears cookie
- **Current user:** `GET /auth/me` — requires auth

### Session cookie

- Name: `access_token`
- Value: JWT (HS256) with claims `sub` (user id), `email`, `iat`, `exp`
- Flags: `HttpOnly`, `SameSite=Lax`, `Secure` when `AUTH_COOKIE_SECURE=1`, `Path=/`

Clients may also send `Authorization: Bearer <token>`. WebSockets accept the cookie or `?token=`.

### Protected surfaces

| Surface | Behavior |
|---------|----------|
| `GET /dashboard` | Redirect to `/login?next=...` if anonymous |
| `GET /assistant`, `/assistant/{id}` | Login required; meeting must belong to the user |
| `/meetings` and `/meetings/{id}/**` | Login required; meeting routes are scoped to the owner (`404` if not yours) |
| `GET/PUT /tuning`, `POST /tuning/reset` | Login required |
| WebSocket `/meetings/{id}/audio` | Cookie or `?token=`; owner only |

Swagger (`/docs`) shows **Authorize** for HTTP Bearer JWT and the `access_token` cookie. After `POST /auth/login`, paste the JWT as Bearer (or into `access_token`) so Try it out works on `/meetings` and `/tuning`.

`GET /health` stays public. HTML/JS assets are public; session pages still redirect to login.

---

### UI pages (`api/web/`)

Served by FastAPI from `api/web/` (not a separate frontend package):

- `login.html` / `register.html` — forms calling `/auth/*`
- `dashboard.html` + `dashboard.js` — list / create / open / delete meetings
- `auth.js` — shared helpers (me, logout, redirects)
- Landing CTA → `/dashboard` (login if needed)

### OAuth / SSO (not implemented)

These always return **501** until implemented:

- `GET /auth/oauth/{google|github|microsoft}`
- `GET /auth/oauth/{provider}/callback`
- `GET /auth/sso/login`
- `POST /auth/sso/callback`

No extra env vars yet for Google/GitHub/Microsoft or SAML.

---

## Production checklist

1. Set a strong unique `JWT_SECRET` (do not use the example placeholder).
2. Set `AUTH_COOKIE_SECURE=1` behind HTTPS.
3. Point `DATABASE_URL` at a managed or private Postgres; do not expose `5432` publicly.
4. Change the default `distill`/`distill` DB password when the database is reachable beyond localhost.
5. Prefer Compose/K8s secrets for `JWT_SECRET` and `DATABASE_URL` instead of committing `.env`.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| API fails on startup with connection errors | Postgres not running, wrong host (`postgres` vs `127.0.0.1`), or bad credentials |
| Login works but cookie ignored | Mixing `http`/`https` or `AUTH_COOKIE_SECURE=1` on plain HTTP |
| `401` on `/meetings` | Missing cookie; register/login first |
| Empty dashboard after upgrade | Meetings created before `user_id` / Postgres have no owner; create new ones |
| pytest cannot create schemas | Role lacks `CREATE` on the database |

Health of the HTTP app remains `GET /health` (does not deep-check Postgres).
