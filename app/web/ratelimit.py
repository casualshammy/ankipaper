"""Redis-backed rate limiting for sensitive endpoints.

The login endpoint is exposed to the public internet, so without a rate
limit it would become a free credential-stuffing / brute-force proxy
against AnkiWeb (see AGENTS.md, audit findings).

We apply two independent counters:

* per-IP (default 5 attempts / minute) — limits brute force from a single
  source;
* per-username (default 10 attempts / hour) — limits credential stuffing
  distributed across many IPs but targeting the same account.

Counters are incremented BEFORE the AnkiWeb call so even invalid
credentials count toward the budget. On a successful login the counters
are cleared so a legitimate user logging in and out is not penalised.

**Fail-closed.** If Redis is unreachable the login attempt is refused
with HTTP 503: it is safer to lock everyone out for a few seconds than
to silently disable brute-force protection. We verify the Redis
connection (ping) on every login request, with one transparent
reconnect attempt, so a transient blip does not lock anyone out.

Requires the optional ``redis`` package (``pip install redis>=5``).
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from fastapi import Request

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


# Atomic INCR + PEXPIRE-on-first-hit. Returns the new counter value.
_LUA_INCR_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class _Bucket:
    """Counter settings for one rate-limit dimension."""

    __slots__ = ("key_prefix", "window_seconds", "max_attempts")

    def __init__(self, key_prefix: str, window_seconds: int, max_attempts: int) -> None:
        self.key_prefix = key_prefix
        self.window_seconds = window_seconds
        self.max_attempts = max_attempts


_client: aioredis.Redis | None = None


async def _new_client() -> aioredis.Redis:
    """Builds and pings a new Redis client."""

    settings = get_settings()
    client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    await client.ping()
    return client


async def _safe_close(client: aioredis.Redis | None) -> None:
    """Best-effort close; swallows errors so it is safe to call repeatedly."""

    if client is None:
        return
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001
        pass


async def _ensure_client() -> aioredis.Redis:
    """Returns a working Redis client.

    On every call verifies the cached connection with ``PING`` and, if it
    has gone stale, transparently reconnects. After two failed attempts
    (initial connect + one reconnect) raises ``RuntimeError`` so the
    caller can decide how to handle the outage — the login route treats
    that as fail-closed.
    """

    global _client
    last_exc: Exception | None = None
    for attempt in (1, 2):
        if _client is None:
            try:
                _client = await _new_client()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Redis connect attempt %d failed: %s", attempt, exc)
                continue
        try:
            await _client.ping()
            return _client
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("Redis ping attempt %d failed: %s", attempt, exc)
            await _safe_close(_client)
            _client = None
    raise RuntimeError(f"Redis unavailable: {last_exc}")


class LoginRateLimiter:
    """Per-IP and per-username counter, backed by Redis."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _buckets(self) -> tuple[_Bucket, _Bucket]:
        """Returns (ip_bucket, user_bucket) using current settings."""

        return (
            _Bucket(
                "rl:login:ip",
                self._settings.login_ip_window_seconds,
                self._settings.login_ip_max_attempts,
            ),
            _Bucket(
                "rl:login:user",
                self._settings.login_user_window_seconds,
                self._settings.login_user_max_attempts,
            ),
        )

    async def check(self, ip: str, username: str) -> str | None:
        """Records an attempt. Returns ``None`` if allowed, error msg if blocked.

        Args:
            ip: client IP (already extracted via X-Forwarded-For when behind
                a proxy).
            username: AnkiWeb username from the form (may be empty).

        Returns:
            Human-readable error message if the attempt exceeds one of the
            configured budgets, otherwise ``None``. The counters are
            already incremented for this attempt — success is the caller's
            responsibility to clear them via :meth:`reset`.

        Raises:
            RuntimeError: if Redis is unreachable (fail-closed signal).
        """

        client = await _ensure_client()
        ip_b, user_b = self._buckets()
        incr = client.register_script(_LUA_INCR_SCRIPT)

        ip_count = await incr(
            keys=[f"{ip_b.key_prefix}:{ip}"],
            args=[ip_b.window_seconds * 1000],
        )
        if ip_count > ip_b.max_attempts:
            logger.warning(
                "Login rate limit hit: ip=%s count=%d max=%d",
                ip,
                ip_count,
                ip_b.max_attempts,
            )
            return _format_error(ip_b, label="IP address")

        if username:
            user_key = f"{user_b.key_prefix}:{username.strip().lower()}"
            user_count = await incr(
                keys=[user_key],
                args=[user_b.window_seconds * 1000],
            )
            if user_count > user_b.max_attempts:
                logger.warning(
                    "Login rate limit hit: user=%s count=%d max=%d",
                    username,
                    user_count,
                    user_b.max_attempts,
                )
                return _format_error(user_b, label="account")

        return None

    async def reset(self, ip: str, username: str) -> None:
        """Best-effort counter clear after a successful login.

        Only the per-user counter for the username that just signed in is
        cleared. The per-IP counter is left alone — it expires naturally
        within ``login_ip_window_seconds`` and clearing it would let an
        attacker who owns a legitimate account use it as a "free reset"
        to brute-force other accounts from the same IP without ever
        hitting the per-IP cap.

        Errors are swallowed: a stale counter from a previous session is
        annoying but not a security problem, and we don't want a Redis
        hiccup to log the user back out.
        """

        if not username:
            return
        try:
            client = await _ensure_client()
            _, user_b = self._buckets()
            user_key = f"{user_b.key_prefix}:{username.strip().lower()}"
            await client.delete(user_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rate limiter reset failed: %s", exc)


def _format_error(bucket: _Bucket, *, label: str) -> str:
    minutes = max(1, bucket.window_seconds // 60)
    unit = "minute" if minutes == 1 else "minutes"
    return (
        f"Too many login attempts from this {label}. "
        f"Please wait {minutes} {unit} and try again."
    )


def get_login_rate_limiter() -> LoginRateLimiter:
    """Returns the rate limiter. Lightweight wrapper, no I/O."""

    return LoginRateLimiter(get_settings())


def client_ip(request: Request, settings: Settings | None = None) -> str:
    """Returns the best-effort client IP for the incoming request.

    When ``behind_proxy`` is true the real client IP is extracted from
    headers set by upstream proxies:

    1. ``CF-Connecting-IP`` (Cloudflare) — preferred, set by CF's edge and
       not spoofable by the client.
    2. First entry of ``X-Forwarded-For`` — used when there is no
       Cloudflare in front. Note that this is *spoofable* by anyone who
       can reach the origin directly (bypassing the proxy). Set up
       Cloudflare (or another proxy whose range you can firewall) so the
       origin IP is not reachable from the open internet.

    Otherwise ``request.client.host`` is used — which, in a Docker setup
    with nginx in front, will be the nginx container's IP and
    effectively rate-limit the entire deployment. Always set
    ``KINDLANKI_BEHIND_PROXY=true`` in production.
    """

    settings = settings or get_settings()
    if settings.behind_proxy:
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            cf_ip = cf_ip.strip()
            if cf_ip:
                return cf_ip
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


async def close_redis() -> None:
    """Closes the Redis connection on application shutdown."""

    global _client
    if _client is not None:
        await _safe_close(_client)
        _client = None