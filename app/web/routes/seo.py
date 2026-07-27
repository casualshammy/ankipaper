"""SEO support routes: ``/robots.txt`` and ``/sitemap.xml``.

These endpoints do not require authentication, do not depend on Redis
and do not touch the Anki collection. They are served even when the
backend is unavailable so that crawlers can keep their view of the site
in sync with the public origin.
"""

from __future__ import annotations

from datetime import date
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from app.config import get_settings
from app.web.seo import resolve_origin

router = APIRouter()

_DISALLOW_PATHS = (
    "/deck/",
    "/sync/",
    "/ms/",
    "/healthz",
    "/logout",
)


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt(request: Request) -> PlainTextResponse:
    """Returns the robots policy.

    Allows crawlers to discover the landing page, ``/login``, and the
    static folder (privacy policy, favicon, OG image). Disallows every
    per-account and per-deck path so search engines never index user
    data.
    """

    base = resolve_origin(request, get_settings())
    sitemap_url = f"{base}/sitemap.xml" if base else "/sitemap.xml"

    lines = ["User-agent: *"]
    lines.append("Allow: /")
    lines.append("Allow: /login")
    lines.append("Allow: /static/")
    for path in _DISALLOW_PATHS:
        lines.append(f"Disallow: {path}")
    lines.append("")
    lines.append(f"Sitemap: {sitemap_url}")
    lines.append("")
    body = "\n".join(lines)
    return PlainTextResponse(content=body, media_type="text/plain; charset=utf-8")


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml(request: Request) -> Response:
    """Returns the XML sitemap listing the public, indexable URLs."""

    base = resolve_origin(request, get_settings())
    today = date.today().isoformat()

    entries = [
        ("/", "weekly"),
        ("/login", "weekly"),
        ("/static/privacy_policy.html", "monthly"),
    ]

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, changefreq in entries:
        loc = f"{base}{path}" if base else path
        parts.append("  <url>")
        parts.append(f"    <loc>{escape(loc)}</loc>")
        parts.append(f"    <lastmod>{today}</lastmod>")
        parts.append(f"    <changefreq>{changefreq}</changefreq>")
        parts.append("  </url>")
    parts.append("</urlset>")
    parts.append("")
    body = "\n".join(parts)
    return Response(content=body, media_type="application/xml; charset=utf-8")
