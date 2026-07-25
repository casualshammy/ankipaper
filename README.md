# kindlanki

Anki client for Amazon Kindle. Lets you sign in to AnkiWeb, browse decks
with statistics and review cards through the built-in Kindle browser
(no JavaScript, monochrome UI tuned for e-ink).

## Stack

- Python 3.11+, FastAPI, Jinja2
- anki 26.x (Rust backend, native Anki collection format)
- SQLite (native Anki format, accessed via `anki.collection` API)
- Docker

## Architecture

One FastAPI process serves a single user. State lives in `/data/*`:

```
data/
├── collection.anki21       # SQLite, native Anki format
├── collection.media/       # card media files
├── hostkey.enc             # Fernet(hostKey), mode 0600
└── session.secret          # Fernet key, mode 0600
```

TLS is handled by a reverse proxy (nginx) and is out of scope for the project.

## Running via Docker

```bash
cp .env.example .env
docker compose -f deploy/compose.yml up -d
```

The service will be available at `http://localhost:8000`. Healthcheck:
`curl http://localhost:8000/healthz`.

## First run

1. Open `http://localhost:8000/login`.
2. Enter your AnkiWeb login and password.
3. After authentication the service downloads your collection (`full_download`).
4. Navigate to `/` to see the list of decks.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e .
uvicorn app.main:app --reload --port 8000
```

## Storage and backup

All data lives under `deploy/data/`. To back up, copy that directory while
the container is stopped.

## Security

- The `hostKey` is stored encrypted with a Fernet key.
- Session cookies are HttpOnly, SameSite=Lax, Secure when running behind a proxy.
- HTTPS is not configured by the project; it must be terminated by a reverse proxy.

## Limitations

- No JavaScript in templates (Kindle WebKit 1.x does not support it).
- The colour palette is tuned for 32 shades of grey.
- One user per instance.
