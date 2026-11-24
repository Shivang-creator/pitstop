"""The link checker fetches URLs written by whoever owns the scanned channel.

So it is a request-forgery primitive unless it refuses to leave the public
internet, and it is the one check a wrong answer discredits the whole report
for. These tests pin both: where it will not go, and what it will not call dead.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pitstop.checks import CheckContext
from pitstop.checks.links import (DeadLinkCheck, LinkStatus, RefusedTarget,
                                  _probe, _refuse_internal, probe_urls)
from pitstop.models import Catalog, Channel, Video

NOW = datetime.now(timezone.utc)


def _video(description: str, vid: str = "v1") -> Video:
    return Video(id=vid, title="T", description=description,
                 published_at=NOW - timedelta(days=30), view_count=3000)


def _catalog(*videos: Video) -> Catalog:
    return Catalog(channel=Channel(id="UC0", title="T"), videos=list(videos))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9001/2018-06-01/runtime/invocation/next",
    "http://localhost:8000/api/scan",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.1/admin",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0/",
])
def test_internal_addresses_are_refused(url):
    with pytest.raises(RefusedTarget):
        asyncio.run(_refuse_internal(httpx.Request("GET", url)))


def test_a_public_address_is_allowed():
    asyncio.run(_refuse_internal(httpx.Request("GET", "http://93.184.216.34/")))


def test_a_loopback_link_in_a_description_is_never_requested():
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/internal"
        findings = list(DeadLinkCheck().run(
            _catalog(_video(f"see {url}")), CheckContext()))
        result = probe_urls([url])
    finally:
        server.shutdown()

    assert hits == []
    assert findings == []          # a 404 from an internal host is not reported
    assert result[url][0] == LinkStatus.UNVERIFIED


def test_a_redirect_into_the_internal_network_is_not_followed():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            302, headers={"Location": "http://169.254.169.254/latest/"})

    async def run():
        async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                event_hooks={"request": [_refuse_internal]}) as client:
            return await _probe(client, "http://93.184.216.34/go",
                                asyncio.Semaphore(1))

    status, code, _ = asyncio.run(run())
    assert status == LinkStatus.UNVERIFIED and code is None
    assert all("169.254" not in url for url in seen)


def _probe_with_error(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    async def run():
        async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)) as client:
            return await _probe(client, "http://93.184.216.34/",
                                asyncio.Semaphore(1))

    return asyncio.run(run())


def test_a_certificate_failure_is_not_reported_as_a_dead_domain():
    status, _, _ = _probe_with_error(httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "unable to get local issuer certificate (_ssl.c:1000)"))
    assert status == LinkStatus.UNVERIFIED


def test_a_name_that_does_not_resolve_is_still_dead():
    status, _, _ = _probe_with_error(httpx.ConnectError(
        "[Errno 8] nodename nor servname provided, or not known"))
    assert status == LinkStatus.DEAD


def test_a_link_repeated_in_one_description_is_one_finding():
    url = "https://example.com/gone"
    video = _video(f"Gear: {url}\n\nlots of text\n\nGear again: {url}.")
    ctx = CheckContext()
    ctx.cache["link_status"] = {url: (LinkStatus.DEAD, 404, url)}

    findings = list(DeadLinkCheck().run(_catalog(video), ctx))

    assert len(findings) == 1
    assert ctx.cache["link_budget"]["unique_urls"] == 1
