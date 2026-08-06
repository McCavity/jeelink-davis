"""
Tests for the revalidation header on the dashboard's own code.

Written after 2026-08-06, when a deployed console page did not reach the kiosk
that was meant to show it. The origin said nothing about freshness, so the
layers above invented an answer: Cloudflare cached the .js by extension and
attached a four-hour browser TTL of its own, and the served file was seven
hours older than the one on the Pi.

The property worth pinning is narrow: the files that ARE the application must
carry a header that forces revalidation, and the API must not — its endpoints
have their own, deliberate caching, and a blanket no-cache here would quietly
undo the countdown that keeps edge and origin expiring in the same instant.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from web import app as web_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client without the lifespan — no serial port, no sensors, no DB."""
    return TestClient(web_app.app)


class TestRevalidation:
    @pytest.mark.parametrize("pfad", [
        "/static/js/console.js",
        "/static/js/weather-core.js",
        "/static/i18n/de.json",
    ])
    def test_static_assets_must_be_revalidated(self, client, pfad):
        antwort = client.get(pfad)
        assert antwort.status_code == 200
        assert antwort.headers["cache-control"] == "no-cache"

    @pytest.mark.parametrize("pfad", ["/", "/console/"])
    def test_html_entry_points_too(self, client, pfad):
        """index.html carries the entire dashboard script inline — caching the
        page is caching the code."""
        antwort = client.get(pfad)
        assert antwort.status_code == 200
        assert antwort.headers["cache-control"] == "no-cache"

    def test_revalidation_is_cheap_because_the_etag_survives(self, client):
        """no-cache means "ask", not "resend". Without the ETag the guarantee
        would cost a full body on every page load instead of a 304."""
        erst = client.get("/static/js/console.js")
        etag = erst.headers.get("etag")
        assert etag, "StaticFiles must still emit an ETag"

        wieder = client.get("/static/js/console.js", headers={"If-None-Match": etag})
        assert wieder.status_code == 304
        assert not wieder.content

    def test_the_api_keeps_its_own_caching(self, client):
        """The aggregate endpoints send a counted-down max-age so that edge and
        origin expire together. A blanket no-cache would undo exactly that, and
        the 26-second query behind it would face the open internet again."""
        antwort = client.get("/api/system")
        assert antwort.headers.get("cache-control") != "no-cache"
