# CLAUDE.md — YouTube AI Crawler

영문 YouTube 채널의 신규 영상을 자동 감지하여 한국어 요약 보고서를 이메일로 발송하는 회원제 웹 플랫폼.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env — at minimum set ENCRYPT_KEY, SMTP_*, and SESSION_SECRET

# Generate ENCRYPT_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Run web server (development)
uvicorn src.web:app --reload

# Run web server (production)
uvicorn src.web:app --host 0.0.0.0 --port $PORT
```

## Project Structure

```
youtube-ai-crawler/
├── src/
│   ├── __init__.py          # Empty package marker
│   ├── core.py              # Shared utilities (DB, crypto, YouTube, AI, email)
│   ├── web.py               # FastAPI web server + all HTTP routes
│   ├── main.py              # CLI entry point (daemon / one-shot mode)
│   └── templates/
│       ├── base.html        # Base layout (nav, session, CSRF injection)
│       ├── login.html       # Login page
│       ├── register.html    # Registration page
│       ├── dashboard.html   # Main dashboard (channels, scanned items, summaries)
│       └── settings.html    # User settings (OpenAI API key, model, prompt)
├── data/
│   └── app.db               # SQLite DB (auto-created on first run)
├── .env.example             # Environment variable template
├── Procfile                 # Railway/Heroku deployment (uvicorn)
├── requirements.txt         # Python dependencies
├── README.md                # Brief Korean overview
└── GUIDE.md                 # Comprehensive Korean usage guide
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| Web framework | FastAPI 0.115.8 + Uvicorn 0.34.0 |
| Templating | Jinja2 3.1.5 |
| Database | SQLite with WAL mode |
| AI / Summarization | OpenAI Chat Completions API (openai 1.68.2) |
| YouTube data | youtube-transcript-api ≥1.1.0, feedparser 6.0.11, yt-dlp ≥2025.10.14 |
| Encryption | cryptography ≥42.0.0 (Fernet AES-128-CBC) |
| Auth / Sessions | itsdangerous ≥2.1.0 + Starlette SessionMiddleware |
| HTTP client | requests 2.32.3 |
| Config | python-dotenv 1.0.1 |
| Forms | python-multipart 0.0.20 |

## Module Responsibilities

### `src/core.py` — Shared Utilities
Pure utility functions; imported by both `web.py` and `main.py`. Contains no FastAPI or CLI-specific code.

Key functions:

| Function | Purpose |
|----------|---------|
| `init_db()` | Create tables + run schema migrations |
| `get_db()` / `db_connection()` | Context managers for SQLite connections |
| `encrypt_value(val)` / `decrypt_value(val)` | Fernet encryption for sensitive strings |
| `hash_password(pw)` / `verify_password(pw, h)` | PBKDF2-SHA256 password hashing |
| `extract_channel_id(input)` | Parse YouTube URL / @handle / channel ID → canonical ID |
| `get_feed(channel_id)` | Fetch YouTube RSS feed via feedparser |
| `fetch_transcript(video_id)` | Get English subtitles with yt-dlp fallback |
| `summarize_korean(transcript, api_key, model, prompt)` | Call OpenAI to generate Korean summary |
| `send_email(...)` | Send HTML+plain SMTP email |
| `now_iso()` | Return current UTC time as ISO 8601 string |

### `src/web.py` — FastAPI Application
All HTTP routes, session management, CSRF protection, and background task scheduling.

**Startup (lifespan):** calls `init_db()`, validates `ENCRYPT_KEY` and warns if `SESSION_SECRET` is unset.

**Background tasks:** uses FastAPI `BackgroundTasks` (non-blocking) to run scan and summary generation without blocking HTTP responses (avoids Railway 503 timeouts).

### `src/main.py` — CLI Entry Point
Standalone daemon / one-shot mode. Uses `user_id=0` as the legacy CLI user.

```bash
python -m src.main add-channel --channel "@Fireship" --email you@example.com
python -m src.main run-once
python -m src.main run --interval 30   # daemon, polls every 30 minutes
```

## Database Schema

Four tables, all created by `init_db()` in `core.py`:

```sql
-- Users
app_users (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  email        TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at   TEXT NOT NULL
)

-- YouTube channels registered per user
user_channels (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL,
  channel_id   TEXT NOT NULL,       -- canonical YouTube channel ID
  channel_name TEXT,
  added_at     TEXT NOT NULL,
  UNIQUE(user_id, channel_id)
)

-- Videos detected from RSS feeds (de-duplication table)
scanned_items (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL,
  channel_id   TEXT NOT NULL,
  video_id     TEXT NOT NULL,
  video_title  TEXT,
  scanned_at   TEXT NOT NULL,
  UNIQUE(user_id, video_id)
)

-- AI-generated Korean summaries
generated_items (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id        INTEGER NOT NULL,
  video_id       TEXT NOT NULL,
  video_title    TEXT,
  summary        TEXT,
  generated_at   TEXT NOT NULL
)
```

> **Note:** An older version of `claude.md` referenced a `sent_items` table — this no longer exists. The current schema uses `scanned_items` + `generated_items`.

## HTTP API Routes

### Auth
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Redirect to `/dashboard` or `/login` |
| GET | `/register` | Registration form |
| POST | `/register` | Create account |
| GET | `/login` | Login form |
| POST | `/login` | Authenticate, set session |
| POST | `/logout` | Clear session |

### Dashboard & Channels
| Method | Path | Description |
|--------|------|-------------|
| GET | `/dashboard` | Channel list + scanned items + summaries (paginated) |
| POST | `/channels/add` | Register a new YouTube channel |
| POST | `/channels/delete` | Remove a channel |
| POST | `/run-now` | Trigger immediate scan (background task) |
| POST | `/generate-summaries` | Queue Korean summary generation (background task) |
| POST | `/generated/delete` | Delete a summary record |

### Settings
| Method | Path | Description |
|--------|------|-------------|
| GET | `/settings` | Settings form (OpenAI key, model, custom prompt) |
| POST | `/settings` | Save settings (API key encrypted before storage) |

### Ops
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check — returns `{"status": "ok"}` |

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `ENCRYPT_KEY` | Fernet key for encrypting OpenAI API keys in the DB. Generate once: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### SMTP (required for email delivery)

| Variable | Example |
|----------|---------|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASSWORD` | Gmail App Password |
| `SMTP_USE_TLS` | `true` (default) |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_SECRET` | Derived from `ENCRYPT_KEY` | Cookie signing key. Auto-derived for multi-replica stability. |
| `POLL_INTERVAL_MINUTES` | `15` | Background scan interval (web mode) |
| `DB_PATH` | `data/app.db` | SQLite file path — set to Railway Volume path for persistence |

### CLI-only (`src/main.py`)

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI key for CLI user (user_id=0) |
| `OPENAI_MODEL` | Model name, e.g. `gpt-4o-mini` |
| `RECIPIENT_EMAIL` | Email address for summary delivery |

## Code Conventions

### Language
- All UI text, error messages, and inline comments are in **Korean** (target audience is Korean speakers)
- Code identifiers (function names, variable names) are in **English**

### Database Access
Always use context managers — never open a raw connection directly:
```python
# Preferred: auto-commit + auto-close
with db_connection() as conn:
    conn.execute("INSERT INTO ...")

# For read-heavy code
with get_db() as conn:
    rows = conn.execute("SELECT ...").fetchall()
```

### Sensitive Values
All user-provided API keys **must** be encrypted before storage and decrypted on retrieval:
```python
from src.core import encrypt_value, decrypt_value

encrypted = encrypt_value(raw_api_key)          # store this
raw_api_key = decrypt_value(encrypted)          # retrieve this
```

Never store plaintext API keys in the database.

### CSRF Protection
All state-changing POST endpoints use the Double-Submit Cookie pattern:
- Templates inject `{{ csrf_token }}` as a hidden form field (via `base.html`)
- `web.py` verifies with `_verify_csrf(request, form)` at the start of each POST handler
- Do **not** add POST endpoints without CSRF verification

### Timestamps
Use `now_iso()` from `core.py` for all timestamps — returns UTC ISO 8601:
```python
from src.core import now_iso
created_at = now_iso()  # e.g. "2025-10-14T03:22:11.123456"
```

### Background Tasks
Long-running operations (scan, summarize) use FastAPI `BackgroundTasks` to avoid HTTP timeout issues on Railway:
```python
@app.post("/run-now")
async def run_now(background_tasks: BackgroundTasks, ...):
    background_tasks.add_task(scan_recent_episodes_for_user, user_id)
    return RedirectResponse("/dashboard", status_code=303)
```

## Pipeline Flow

```
YouTube RSS Feed
  └─► feedparser → detect new video_ids not in scanned_items
        └─► youtube-transcript-api → fetch English transcript
              └─► (fallback) yt-dlp → extract transcript if API fails
                    └─► OpenAI Chat Completions → Korean summary
                          └─► SMTP → HTML + plain-text email
                                └─► INSERT INTO generated_items (de-dup guard)
```

## Security Design

| Concern | Implementation |
|---------|---------------|
| Password storage | PBKDF2-SHA256, 240,000 iterations, random salt |
| API key storage | Fernet AES-128-CBC symmetric encryption (`ENCRYPT_KEY`) |
| Session integrity | Starlette SessionMiddleware with HMAC signing (`SESSION_SECRET`) |
| CSRF | Double-Submit Cookie pattern on all POST endpoints |
| Multi-replica sessions | `SESSION_SECRET` derived deterministically from `ENCRYPT_KEY` so all replicas share the same signing key |

## Deployment (Railway)

```
# Procfile
web: uvicorn src.web:app --host 0.0.0.0 --port $PORT
```

**Persistent storage:** Mount a Railway Volume at `/data` and set `DB_PATH=/data/app.db` so the SQLite database survives redeploys.

**Required Railway environment variables:**
- `ENCRYPT_KEY` (generate locally, paste into Railway)
- All `SMTP_*` variables
- `DB_PATH=/data/app.db`

## No Test Suite

There is currently no automated test suite. When adding tests:
- Use `pytest` (not yet in `requirements.txt` — add it)
- Database tests should use a temporary in-memory or temp-file SQLite DB
- Mock `send_email()` and `summarize_korean()` to avoid real network calls
