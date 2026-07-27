"""SEO helpers exposed to Jinja templates and the ``/robots.txt``,
``/sitemap.xml`` routes.

These helpers resolve the public origin used in canonical and Open Graph
URLs. When ``settings.public_url`` is configured, that origin wins. When
it is empty we fall back to the scheme/host of the incoming request —
but only when the app is configured to trust ``behind_proxy`` headers;
otherwise we return a path-only URL so self-hosted deployments that
have not set ``ANKIPAPER_PUBLIC_URL`` do not accidentally publish the
internal reverse-proxy origin.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

from app.config import Settings, get_settings


def resolve_origin(request: "Request", settings: Settings) -> str:
    """Returns the absolute origin (scheme + host) for canonical/OG/SEO URLs.

    Returns an empty string when no public origin is configured and the
    request cannot be trusted as a public origin (i.e. when
    ``behind_proxy`` is False). Callers then render path-only URLs.
    """

    public = (settings.public_url or "").rstrip("/")
    if public:
        return public
    if not settings.behind_proxy:
        return ""
    return f"{request.url.scheme}://{request.url.netloc}"


def canonical_url(request: "Request") -> str:
    """Returns the absolute canonical URL for the current request.

    The query string is intentionally stripped — canonical URLs identify
    the resource, not the per-request state. Authenticated routes are
    ``noindex``d in the templates, so the loss of e.g.
    ``?rebuild_ok=...`` signals does not affect SEO.
    """

    settings = get_settings()
    base = resolve_origin(request, settings)
    return f"{base}{request.url.path}"


def og_image_url(request: "Request") -> str:
    """Returns the absolute URL to the Open Graph image (``/static/og.png``).

    Falls back to a path-only URL when no public origin is configured;
    some social-card consumers (Twitter, Slack) require absolute URLs,
    so deployments that need rich link previews should set
    ``ANKIPAPER_PUBLIC_URL``.
    """

    settings = get_settings()
    base = resolve_origin(request, settings)
    return f"{base}/static/og.png"


def webapplication_jsonld(request: "Request") -> str:
    """Returns the JSON-LD ``WebApplication`` block for the landing page.

    Embedded as ``<script type="application/ld+json">`` in the landing
    template via the ``structured_data`` block in ``base.html``.
    """

    settings = get_settings()
    payload = {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": settings.brand_name,
        "url": canonical_url(request),
        "description": settings.meta_description,
        "applicationCategory": "EducationalApplication",
        "operatingSystem": (
            "Any modern browser (Kindle, Kobo, Android, iOS, desktop)"
        ),
        "browserRequirements": "JavaScript enabled",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "image": og_image_url(request),
        "author": {
            "@type": "Person",
            "name": "casualshammy",
            "url": "https://github.com/casualshammy",
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
