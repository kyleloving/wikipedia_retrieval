"""Tests for src.wikipedia_tool with the MediaWiki API mocked.

Run from the project root: python -m pytest tests/test_wikipedia_tool.py

The two-step MediaWiki flow (list=search, then prop=extracts|info) is replaced by
a fake _get that returns canned responses, so these tests never hit the network.
"""

import pytest
import requests

from src import wikipedia_tool


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get_factory(hits, pages, counter=None):
    """Return a fake _get that answers the search call then the detail call."""

    def fake_get(params):
        if counter is not None:
            counter.append(params)
        if params.get("list") == "search":
            return FakeResp({"query": {"search": hits}})
        return FakeResp({"query": {"pages": pages}})

    return fake_get


@pytest.fixture(autouse=True)
def _clear_cache():
    wikipedia_tool._CACHE.clear()
    yield
    wikipedia_tool._CACHE.clear()


def test_successful_retrieval(monkeypatch):
    hits = [
        {
            "pageid": 1,
            "title": "Ada Lovelace",
            "snippet": "English <span>mathematician</span>",
        },
        {"pageid": 2, "title": "Analytical Engine", "snippet": "a machine"},
    ]
    pages = {
        "1": {
            "fullurl": "https://en.wikipedia.org/wiki/Ada_Lovelace",
            "extract": "Ada was a writer.",
        },
        "2": {
            "fullurl": "https://en.wikipedia.org/wiki/Analytical_Engine",
            "extract": "Babbage's design.",
        },
    }
    monkeypatch.setattr(wikipedia_tool, "_get", _fake_get_factory(hits, pages))

    result = wikipedia_tool.wikipedia_search("Ada Lovelace")

    assert result["error"] is None
    assert len(result["results"]) == 2
    first = result["results"][0]
    assert first["title"] == "Ada Lovelace"
    assert first["pageid"] == 1
    assert first["url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"
    assert first["extract"] == "Ada was a writer."
    # HTML markup is stripped from the snippet.
    assert first["snippet"] == "English mathematician"


def test_empty_query_is_error_without_network(monkeypatch):
    called = []
    monkeypatch.setattr(wikipedia_tool, "_get", lambda p: called.append(p))
    result = wikipedia_tool.wikipedia_search("   ")
    assert result["error"] == "empty query"
    assert result["results"] == []
    assert called == []  # never hit the API


def test_no_hits_is_not_an_error(monkeypatch):
    monkeypatch.setattr(wikipedia_tool, "_get", _fake_get_factory([], {}))
    result = wikipedia_tool.wikipedia_search("asdkjhqwekjh")
    assert result["error"] is None
    assert result["results"] == []


def test_timeout_is_captured(monkeypatch):
    def boom(params):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(wikipedia_tool, "_get", boom)
    result = wikipedia_tool.wikipedia_search("anything")
    assert result["results"] == []
    assert "timeout" in result["error"]


def test_request_exception_is_captured(monkeypatch):
    def boom(params):
        raise requests.exceptions.ConnectionError("no route")

    monkeypatch.setattr(wikipedia_tool, "_get", boom)
    result = wikipedia_tool.wikipedia_search("anything")
    assert result["results"] == []
    assert "request failed" in result["error"]


def test_invalid_json_is_captured(monkeypatch):
    class BadResp(FakeResp):
        def json(self):
            raise ValueError("no json")

    monkeypatch.setattr(wikipedia_tool, "_get", lambda p: BadResp({}))
    result = wikipedia_tool.wikipedia_search("anything")
    assert result["results"] == []
    assert "invalid response" in result["error"]


def test_url_falls_back_to_curid_when_missing(monkeypatch):
    hits = [{"pageid": 42, "title": "Thing", "snippet": "s"}]
    pages = {"42": {"extract": "e"}}  # no fullurl
    monkeypatch.setattr(wikipedia_tool, "_get", _fake_get_factory(hits, pages))
    result = wikipedia_tool.wikipedia_search("thing")
    assert result["results"][0]["url"] == "https://en.wikipedia.org/?curid=42"


def test_results_follow_search_ranking(monkeypatch):
    # Detail payload is returned out of order; results must follow hit order.
    hits = [
        {"pageid": 10, "title": "First", "snippet": "a"},
        {"pageid": 20, "title": "Second", "snippet": "b"},
        {"pageid": 30, "title": "Third", "snippet": "c"},
    ]
    pages = {
        "30": {"extract": "z"},
        "10": {"extract": "x"},
        "20": {"extract": "y"},
    }
    monkeypatch.setattr(wikipedia_tool, "_get", _fake_get_factory(hits, pages))
    result = wikipedia_tool.wikipedia_search("ordering")
    assert [r["title"] for r in result["results"]] == ["First", "Second", "Third"]


def test_cache_avoids_second_fetch_and_is_isolated(monkeypatch):
    hits = [{"pageid": 1, "title": "Gold", "snippet": "Au"}]
    pages = {"1": {"fullurl": "u", "extract": "element"}}
    counter = []
    monkeypatch.setattr(wikipedia_tool, "_get", _fake_get_factory(hits, pages, counter))

    r1 = wikipedia_tool.wikipedia_search("gold")
    calls_after_first = len(counter)
    r2 = wikipedia_tool.wikipedia_search("gold")  # served from cache
    assert len(counter) == calls_after_first  # no extra API calls

    # Returned objects are deep copies: distinct identity, equal value.
    assert r1 == r2
    assert r1 is not r2
    assert r1["results"] is not r2["results"]

    # Mutating a returned result must not corrupt the cache.
    r1["results"].append({"title": "junk"})
    r3 = wikipedia_tool.wikipedia_search("gold")
    assert len(r3["results"]) == 1


def test_strip_html_unescapes_entities():
    assert wikipedia_tool._strip_html("a <span>b</span> &amp; c") == "a b & c"
    assert wikipedia_tool._strip_html("") == ""
