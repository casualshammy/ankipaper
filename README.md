# kindlanki

Anki client for Amazon Kindle. Lets you sign in to AnkiWeb, browse decks with statistics, and review cards through the built-in Kindle browser — no JS in notes, no colour UI, only what reads well on e-ink.

Built on top of the official AnkiWeb sync protocol with the `anki` 25.6+ Python package (which exposes the same Rust backend the desktop client uses).

## Features

- AnkiWeb login with persistent cookie session, multiple accounts per instance.
- Manual and auto-sync:
  - incremental collection sync via the Rust backend;
  - full download fallback when the local collection is empty;
  - background media sync with a progress indicator on the top bar.
- Deck list with per-deck `New / Learn / Review` counts from the FSRS scheduler.
- Card review with `Again / Hard / Good / Easy` ratings and interval preview under each button.
- Filtered (cram) decks with a `Rebuild` button.
- Best-effort sync after the last card in a deck is reviewed.
- E-ink first: no JS in notes (`<script>` strips out), only images get downloaded (audio/video/fonts are filtered), Cloze styling overridden for monochrome.

## Stack

- Python 3.11+, FastAPI, Jinja2, uvicorn
- `anki>=25.6` (Rust backend, native Anki collection format)
- `cryptography` + Fernet for cookie/hostKey encryption, `itsdangerous` for cookie signing
- `zstandard` for the AnkiWeb media-sync wire format
- `pydantic-settings` for configuration
- SQLite (native Anki format, accessed through `anki.collection.Collection` and the Rust scheduler backend)
- Docker

TLS is handled by a reverse proxy (nginx) — the project ships an HTTP-only image.

## Running via Docker

```bash
cp .env.example .env
docker compose up --build
```

The service is available at `http://localhost:8000`.

## Configuration

All settings come from environment variables (or `.env`) with the `KINDLANKI_` prefix:

| Variable | Default | Purpose |
|----------|---------|---------|
| `KINDLANKI_BASE_URL` | `http://localhost:8000` | Public URL used for absolute links. |
| `KINDLANKI_COOKIE_MAX_AGE_DAYS` | `30` | Cookie session lifetime. |
| `KINDLANKI_BEHIND_PROXY` | `false` | Set `true` when running behind nginx — enables `Secure` cookies and trusts `X-Forwarded-Proto`. |

## First run

1. Open `http://localhost:8000/login`.
2. Enter your AnkiWeb login and password.
3. Press **Sync** in the top bar — the first sync does an incremental pull, followed by a full download if the local collection is empty, then a background media sync.
4. Open a deck from the home page and start reviewing.

If your hostKey ever expires, AnkiWeb rejects the sync and you are redirected back to login page.

## On-disk layout

All state lives in the `/data` directory in the container:

```
data/
├── session.secret                              # Fernet key for cookies and hostKey encryption (mode 0600)
└── accounts/
    └── <account_id>/                           # sanitised AnkiWeb username, one directory per account
        ├── collection.anki21                   # SQLite, native Anki format
        ├── collection.media/                   # downloaded media (images only)
        ├── hostkey.enc                         # Fernet(AnkiWeb hostKey), mode 0600
        └── media.last_usn                      # last AnkiWeb USN processed by media sync
```

Log out does **not** delete an account — sign back in to recover it.

## Limitations

- Cards are not editable.
- Notes that depend on JavaScript will render with the script tags stripped.
- Only images (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`) are downloaded and served — audio, video and fonts are filtered out before sync.
- Kindle-specific quirks: CSS is kept simple, no border-radius or shadows, only black/white plus a handful of grey shades, links are forced to black even when visited.
- Almost all rendering is done server-side - so offline access is not possible.
