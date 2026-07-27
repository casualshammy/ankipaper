# AnkiPaper

<img src="app/web/static/app_icon_256.webp" alt="AnkiPaper" width="96" align="right">

AnkiWeb client for Amazon Kindle and other e-ink browsers. AnkiPaper lets you sign in to AnkiWeb, browse decks, and review cards in a server-rendered interface designed for slow monochrome devices.

It uses Anki's native collection format, the official AnkiWeb sync protocol, and the same Rust scheduler backend used by Anki Desktop. Card editing, analytics, and heavy client-side UI are intentionally out of scope.

## Features

- AnkiWeb login with persistent, signed cookie sessions.
- Multiple AnkiWeb accounts per instance, isolated in separate data directories.
- Incremental collection sync through Anki's Rust backend.
- Explicit full-sync flow:
  - detects download, upload, and conflict states;
  - asks the user to choose and confirm a destructive direction when required;
  - runs media sync in the background after the collection sync.
- Sync status with a progress indicator and a pending-changes marker.
- Per-deck `New / Learn / Review` counts from the FSRS scheduler.
- Card review with `Again / Hard / Good / Easy` ratings and interval previews.
- Mark and flag controls in the study toolbar.
- Filtered (cram) deck rebuilding.
- Best-effort sync after the last card in a deck is reviewed.
- E-ink rendering:
  - card scripts are stripped;
  - audio, video, and unsupported embeds are removed;
  - only images and fonts are synchronized;
  - cloze styling and visited links are adjusted for monochrome screens.

## Limitations

- Cards and notes cannot be edited.
- There is no card browser or analytics dashboard.
- JavaScript-dependent notes may lose functionality because script tags are removed.
- Only `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.otf`, `.ttf`, `.woff`, and `.woff2` files are synchronized. Audio, video, JavaScript, and other media are intentionally skipped.
- Media files above the configured per-file or collection limits are not downloaded.
- Most rendering is server-side. The only routine client-side behavior is sync-status polling on the home page, so offline study is not supported.
- Kindle-specific CSS is deliberately simple: no color-dependent UI, animations, shadows, or complex responsive behavior.
- AnkiWeb credentials and deck data pass through the server. Self-host the application if that is not acceptable for your threat model.
- There is currently no in-app account deletion flow; remove an account's data directory only with the application stopped and an appropriate backup in place.

## Legal

AnkiPaper is an unofficial third-party client. It is not affiliated with Ankitects Pty Ltd and is not on the list of approved clients in the [AnkiWeb Terms of Service](https://ankiweb.net/account/terms).

The AnkiWeb ToS restricts direct access to the sync service to approved clients (Anki Desktop, AnkiMobile, AnkiDroid, AnkiUniversal). AnkiPaper uses that same sync protocol to talk to AnkiWeb directly, which is outside the approved-clients list. Ankitects have historically tolerated small personal-use clients that only sync their own data and do not add noticeable load, but they may change their policy or suspend accounts at their sole discretion.

Practical implications:

- Your AnkiWeb account may be suspended or terminated by Ankitects for any reason, including the use of AnkiPaper. This is at Ankitects' sole discretion.
- If Ankitects block AnkiPaper on their servers, the application will stop working. The author will not try to circumvent such a block.
- Use is at your own risk. Keep a local backup of your collection.

## Stack

- Python 3.11+ (FastAPI, Jinja2, Uvicorn)
- Redis
- Docker Compose

The application image serves HTTP on port `8000`. TLS is expected to be terminated by an external reverse proxy.

## Quick start with Docker Compose

Clone repository. Then from the repository root:

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml --project-directory . up --build
```

The Compose stack starts:

- `ankipaper` — FastAPI application on `127.0.0.1:8000`;
- `redis` — Redis rate-limit backend.

The application is available at `http://localhost:8000`. Persistent data is stored in `./.data/`; Redis persistence is stored in `./.redis/`.

## Configuration

Settings are read from environment variables or `.env` using the `ANKIPAPER_` prefix. `.env.example` contains a Docker-ready Redis URL; the application code default is intended for local execution outside Compose.

| Variable | Default | Purpose |
|---|---:|---|
| `ANKIPAPER_COOKIE_MAX_AGE_DAYS` | `30` | Signed session lifetime in days. |
| `ANKIPAPER_BEHIND_PROXY` | `false` | Set to `true` behind nginx or another trusted reverse proxy. Enables `Secure` cookies and proxy-aware client IP detection. |
| `ANKIPAPER_SHOW_PRIVACY_POLICY` | `false` | Adds a link to `/static/privacy_policy.html` in login and deck-list footers. |
| `ANKIPAPER_DEBUG_HEADERS` | `false` | Logs all incoming request headers for proxy debugging. Keep disabled in production because cookies and authorization headers may be logged. |
| `ANKIPAPER_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for login and sync rate limiting. In Docker Compose use `redis://redis:6379/0`. |
| `ANKIPAPER_LOGIN_IP_MAX_ATTEMPTS` | `5` | Maximum login attempts per IP in the IP window. |
| `ANKIPAPER_LOGIN_IP_WINDOW_SECONDS` | `60` | Login rate-limit window per IP. |
| `ANKIPAPER_LOGIN_USER_MAX_ATTEMPTS` | `10` | Maximum login attempts per username in the username window. |
| `ANKIPAPER_LOGIN_USER_WINDOW_SECONDS` | `3600` | Login rate-limit window per username. |
| `ANKIPAPER_DATA_MAX_BYTES` | `0` | Maximum total `/data` size for creating new accounts. `0` disables the limit; existing accounts remain accessible. |
| `ANKIPAPER_MEDIA_MAX_FILE_BYTES` | `1048576` (1 MiB) | Maximum size of one media file. Larger files are skipped during media sync. |
| `ANKIPAPER_MEDIA_MAX_COLLECTION_BYTES` | `209715200` (200 MiB) | Maximum size of `collection.media/`. When the existing directory is at or above the limit, new media files are not written. |

Redis is required for protected login and sync operations. If Redis is unavailable, rate limiting fails closed: login returns an error and sync is blocked rather than running without protection.

## Reverse proxy and production notes

The Compose service binds port `8000` to loopback only. Put nginx, Cloudflare Tunnel, or another TLS reverse proxy in front of it for external access.

For a reverse-proxy deployment:

1. Set `ANKIPAPER_BEHIND_PROXY=true`.
2. Make sure `X-Forwarded-Proto` header contains the real client IP. The application also understands Cloudflare's `CF-Connecting-IP` header when proxy mode is enabled.

`deploy/nginx.conf` includes the example of reverse-proxy locations and security headers expected by the application. Configure TLS certificates and the public hostname for your environment before using it.
