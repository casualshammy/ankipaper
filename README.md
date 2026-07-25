# kindlanki

Anki client for Amazon Kindle. Lets you sign in to AnkiWeb, browse decks with statistics, and review cards through the built-in Kindle browser — no JS in notes, no colour UI, only what reads well on e-ink.

Built on top of the official AnkiWeb sync protocol with the `anki` 25.6+ Python package (which exposes the same Rust backend the desktop client uses).

## Features

- AnkiWeb login with persistent cookie session, multiple accounts per instance (isolated per directory).
- Manual and auto-sync:
  - incremental collection sync via the Rust backend;
  - full download fallback when the local collection is empty;
  - background media sync with a progress indicator on the top bar.
- Deck list with per-deck `New / Learn / Review` counts from the FSRS scheduler.
- Card review with `Again / Hard / Good / Easy` ratings and interval preview under each button.
- Filtered (cram) decks with a `Rebuild` button.
- Best-effort sync after the last card in a deck is reviewed.
- E-ink first: `<script>` blocks stripped from notes, audio/video omitted, only images and fonts are downloaded, Cloze styling overridden for monochrome.
- Rate limiting (per-IP and per-username) backed by Redis.

## Stack

- Python 3.11+, FastAPI, Jinja2, uvicorn, python-multipart
- `anki>=25.6` (Rust backend, native Anki collection format)
- `cryptography` (Fernet) for cookie/hostKey encryption, `itsdangerous` for cookie signing
- `zstandard` for the AnkiWeb media-sync wire format
- `pydantic-settings` for configuration
- Redis 7 for rate limiting
- SQLite (native Anki format, accessed through `anki.collection.Collection` and the Rust scheduler backend)
- Docker Compose + nginx (TLS terminator)

TLS is handled by a reverse proxy (nginx) — the project ships an HTTP-only image.

## Running locally via Docker Compose

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml --project-directory "." up --build
```

Two services come up:

- `kindlanki` — the FastAPI app on port `8000`.
- `redis` — rate-limit backend (Alpine, healthchecked).

The application is available at `http://localhost:8000`. State is persisted in `./.data/` and `./.redis/`.

## Configuration

All settings come from environment variables (or `.env`) with the `KINDLANKI_` prefix:

| Variable | Default | Purpose |
|----------|---------|---------|
| `KINDLANKI_BASE_URL` | `http://localhost:8000` | Public URL used for absolute links. |
| `KINDLANKI_COOKIE_MAX_AGE_DAYS` | `30` | Cookie session lifetime. |
| `KINDLANKI_BEHIND_PROXY` | `false` | Set `true` behind nginx — enables `Secure` cookies and trusts `X-Forwarded-Proto`. |
| `KINDLANKI_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL for rate limiting. |
| `KINDLANKI_LOGIN_IP_MAX_ATTEMPTS` | `5` | Max failed logins per IP within the IP window. |
| `KINDLANKI_LOGIN_IP_WINDOW_SECONDS` | `60` | Rolling window for the per-IP limit. |
| `KINDLANKI_LOGIN_USER_MAX_ATTEMPTS` | `10` | Max failed logins per username within the user window. |
| `KINDLANKI_LOGIN_USER_WINDOW_SECONDS` | `3600` | Rolling window for the per-username limit. |
| `KINDLANKI_MEDIA_MAX_FILE_BYTES` | `1048576` (1 MiB) | Per-file media size cap during media-sync. |
| `KINDLANKI_MEDIA_MAX_COLLECTION_BYTES` | `209715200` (200 MiB) | Total `collection.media/` cap; existing directories over the cap are not extended. |

## First run

1. Open `http://localhost:8000/login`.
2. Enter your AnkiWeb login and password.
3. Press **Sync** in the top bar — the first sync does an incremental pull, followed by a full download if the local collection is empty, then a background media sync.
4. Open a deck from the home page and start reviewing.

If your hostKey ever expires, AnkiWeb rejects the sync and you are redirected back to the login page.

## On-disk layout

All state lives under `/data` in the container (mount point `./.data`):

```
data/
├── session.secret                          # Fernet key for cookie+CSRF signing (mode 0600)
├── hostkey.secret                          # Fernet key for per-account hostKey encryption (mode 0600)
└── accounts/
    └── <account_id>/                       # sanitised AnkiWeb username, one directory per account
        ├── collection.anki21               # SQLite, native Anki format
        ├── collection.media/               # downloaded images and fonts
        ├── hostkey.enc                     # Fernet(AnkiWeb hostKey), mode 0600
        └── media.last_usn                  # last AnkiWeb USN processed by media sync
```

The two secret files are kept separate so a leak of one does not compromise the other. Back up the whole `data/` directory (including both `*.secret` files) to keep accounts restorable.

### Multiple accounts

Each AnkiWeb username gets its own directory under `data/accounts/`. Cookie sessions are isolated per account, and login is bound to the sanitised username — signing in with a different user switches accounts without touching the others. Log out does **not** delete an account — sign back in to recover it.

## Limitations

- Cards are not editable.
- Notes that depend on JavaScript will render with the script tags stripped.
- Only images (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`) and fonts (`.otf`, `.ttf`, `.woff`, `.woff2`) are downloaded and served — audio, video and other media are filtered out before sync.
- Kindle-specific quirks: CSS is kept simple, no border-radius or shadows, only black/white plus a handful of grey shades, visited links are forced to black.
- Almost all rendering is done server-side, so offline access is not possible.