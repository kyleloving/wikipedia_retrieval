"""Local Wikipedia retrieval, independent of Claude.

Queries the English Wikipedia MediaWiki API over HTTP and returns structured
evidence (titles, page IDs, URLs, snippets, intro extracts). It retrieves only;
it does not synthesize answers.

Smoke test:
    python -m src.wikipedia_tool "Ada Lovelace"
"""

import copy
import html
import json
import re
import sys
import threading
import time

import requests
from anthropic import beta_tool
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

API_URL = "https://en.wikipedia.org/w/api.php"
TIMEOUT_SECONDS = 10
USER_AGENT = "claude-wikipedia-qa/0.1 (local educational project)"

# Politeness + resilience for the Wikipedia API.
MAX_RETRIES = 4  # retries on 429 / transient 5xx
BACKOFF_FACTOR = 0.5  # exponential backoff between retries: 0.5s, 1s, 2s, ...
MIN_REQUEST_INTERVAL_SECONDS = 0.5  # minimum spacing between outbound requests

# Bumped when the model-facing tool's name, signature, or returned shape changes.
TOOL_SCHEMA_VERSION = "v1"

# Simple in-memory cache of successful lookups, keyed by (lowercased query, top_k).
# Stored and returned values are deep-copied so callers can't mutate cached state.
_CACHE: dict = {}

# One shared session. The adapter retries 429 and transient 5xx responses with
# exponential backoff, honoring any Retry-After header. A process-wide throttle
# (below) spaces requests out so we avoid tripping the rate limit in the first
# place; together they keep eval runs from being polluted by 429s.
_RETRY = Retry(
    total=MAX_RETRIES,
    backoff_factor=BACKOFF_FACTOR,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=True,
    raise_on_status=False,  # return the final response; raise_for_status handles it
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})
_ADAPTER = HTTPAdapter(max_retries=_RETRY)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)

_throttle_lock = threading.Lock()
_last_request_at = 0.0


def _get(params: dict):
    """GET API_URL, throttled to at most one request per interval.

    Retry/backoff on 429 and transient 5xx is handled by the session adapter.
    """
    global _last_request_at
    with _throttle_lock:
        wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
    return _SESSION.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)


def _strip_html(text: str) -> str:
    """Remove the <span class="searchmatch"> markup Wikipedia puts in snippets."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def wikipedia_search(query: str, top_k: int = 5) -> dict:
    """Search English Wikipedia and return structured results.

    Returns {"query", "results", "error"} where each result has
    {"title", "pageid", "url", "snippet", "extract"}. On a network/API failure
    "results" is empty and "error" holds a message — this function does not
    raise for retrieval problems. No results (a valid but empty search) is not
    an error: "results" is empty and "error" is None.
    """
    query = (query or "").strip()
    result = {"query": query, "results": [], "error": None}

    if not query:
        result["error"] = "empty query"
        return result

    cache_key = (query.lower(), top_k)
    if cache_key in _CACHE:
        return copy.deepcopy(_CACHE[cache_key])

    try:
        search_resp = _get(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": top_k,
                "format": "json",
            }
        )
        search_resp.raise_for_status()
        hits = search_resp.json().get("query", {}).get("search", [])

        if not hits:
            _CACHE[cache_key] = copy.deepcopy(result)
            return result

        pageids = [str(hit["pageid"]) for hit in hits]
        detail_resp = _get(
            {
                "action": "query",
                "pageids": "|".join(pageids),
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "exlimit": "max",
                "inprop": "url",
                "format": "json",
            }
        )
        detail_resp.raise_for_status()
        pages = detail_resp.json().get("query", {}).get("pages", {})

    except requests.exceptions.Timeout:
        result["error"] = f"timeout after {TIMEOUT_SECONDS}s"
        return result
    except requests.exceptions.RequestException as e:
        result["error"] = f"request failed: {e}"
        return result
    except ValueError as e:  # JSON decode failure
        result["error"] = f"invalid response: {e}"
        return result

    # Merge detail into the search ranking order.
    for hit in hits:
        pageid = hit["pageid"]
        page = pages.get(str(pageid), {})
        result["results"].append(
            {
                "title": hit.get("title"),
                "pageid": pageid,
                "url": page.get("fullurl")
                or f"https://en.wikipedia.org/?curid={pageid}",
                "snippet": _strip_html(hit.get("snippet", "")) or None,
                "extract": (page.get("extract") or "").strip() or None,
            }
        )

    _CACHE[cache_key] = copy.deepcopy(result)
    return result


def make_search_wikipedia_tool(recorder=None):
    """Build the model-facing search_wikipedia tool.

    If `recorder` (a list) is given, each call appends a record of the tool's
    input, output, and latency. This is execution-time trace capture at the
    tool boundary — it observes what Claude actually sent and received without
    altering the runner's message loop.
    """

    @beta_tool
    def search_wikipedia(query: str) -> str:
        """Search English Wikipedia for factual information.

        Call this whenever answering needs facts that can be verified — people,
        places, dates, definitions, events, or "who/what/when/where" questions.
        Do not answer factual questions from prior knowledge; search first.

        Args:
            query: A few keywords or a short phrase to search for.

        Returns:
            A JSON string with the top results, each having title, pageid, url,
            snippet, and an intro extract; an "error" field is set on failure.
        """
        started = time.monotonic()
        output = json.dumps(wikipedia_search(query), ensure_ascii=False)
        if recorder is not None:
            recorder.append(
                {
                    "name": "search_wikipedia",
                    "input": {"query": query},
                    "output": output,
                    "latency_s": time.monotonic() - started,
                }
            )
        return output

    return search_wikipedia


# Default standalone instance (no recording) for direct use.
search_wikipedia = make_search_wikipedia_tool()


def _main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print('usage: python -m src.wikipedia_tool "<query>" [top_k]', file=sys.stderr)
        return 2
    query = args[0]
    top_k = int(args[1]) if len(args) > 1 else 5
    print(json.dumps(wikipedia_search(query, top_k), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
