"""The public endpoint spends a shared, finite API quota for anonymous callers.

These tests pin the ceilings that stop one caller spending all of it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pitstop.web_app as web_app
from pitstop.public_scan import PublicScanError


@pytest.fixture()
def app_state(monkeypatch):
    """A clean limiter, and a scan that never touches the network."""
    calls: list[str] = []

    def fake_scan(channel, **kwargs):
        calls.append(channel)
        raise PublicScanError("not_found", f"No channel for {channel}")

    monkeypatch.setattr(web_app, "public_scan", fake_scan)
    monkeypatch.setattr(web_app, "_HITS", {})
    monkeypatch.setattr(web_app, "_SPENT", {"day": "", "units": 0})
    monkeypatch.setattr(web_app, "_CACHE", {})
    return calls


def test_one_channel_typed_five_ways_is_one_cache_entry():
    keys = {web_app._cache_key(ref) for ref in (
        "@MKBHD", "@mkbhd", "mkbhd", "https://www.youtube.com/@mkbhd",
        "https://youtube.com/@MKBHD/videos")}
    assert len(keys) == 1


def test_channel_ids_keep_their_case_in_the_cache_key():
    a = web_app._cache_key("UCBJycsmduvYEL83R_U4JriQ")
    b = web_app._cache_key("UCBJYCSMDUVYEL83R_U4JRIQ")
    assert a != b


def test_a_caller_is_cut_off_before_the_scan_runs(app_state, monkeypatch):
    monkeypatch.setattr(web_app, "RATE_PER_WINDOW", 3)
    client = TestClient(web_app.app)
    headers = {"x-real-ip": "203.0.113.7"}

    codes = [client.post("/api/scan.json", json={"channel": f"@nobody{i}"},
                         headers=headers).status_code for i in range(5)]

    assert codes == [404, 404, 404, 429, 429]
    assert len(app_state) == 3     # the refused two never reached YouTube
    refused = client.post("/api/scan.json", json={"channel": "@x"},
                          headers=headers).json()
    assert refused["error"] == "rate"

    # Somebody else is unaffected.
    other = client.post("/api/scan.json", json={"channel": "@x"},
                        headers={"x-real-ip": "198.51.100.9"})
    assert other.status_code == 404


def test_the_streaming_endpoint_is_limited_too(app_state, monkeypatch):
    monkeypatch.setattr(web_app, "RATE_PER_WINDOW", 1)
    client = TestClient(web_app.app)
    headers = {"x-real-ip": "203.0.113.8"}

    assert client.post("/api/scan", json={"channel": "@a"},
                       headers=headers).status_code == 200
    second = client.post("/api/scan", json={"channel": "@b"}, headers=headers)

    assert second.status_code == 429
    assert app_state == ["@a"]


def test_a_spent_daily_allowance_stops_new_scans(app_state, monkeypatch):
    monkeypatch.setattr(web_app, "DAILY_UNIT_CEILING", 10)
    web_app._record_spend(10)
    client = TestClient(web_app.app)

    response = client.post("/api/scan.json", json={"channel": "@anyone"})

    assert response.status_code == 429
    assert response.json()["error"] == "quota"
    assert app_state == []


def test_failed_lookups_count_against_the_allowance(app_state):
    client = TestClient(web_app.app)
    client.post("/api/scan.json", json={"channel": "@nobody"})
    assert web_app._SPENT["units"] == web_app.FAILED_SCAN_UNITS
