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

import requests
from anthropic import beta_tool

API_URL = "https://en.wikipedia.org/w/api.php"
TIMEOUT_SECONDS = 10
USER_AGENT = "claude-wikipedia-qa/0.1 (local educational project)"

# Simple in-memory cache of successful lookups, keyed by (lowercased query, top_k).
# Stored and returned values are deep-copied so callers can't mutate cached state.
_CACHE: dict = {}


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

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        search_resp = session.get(
            API_URL,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": top_k,
                "format": "json",
            },
            timeout=TIMEOUT_SECONDS,
        )
        search_resp.raise_for_status()
        hits = search_resp.json().get("query", {}).get("search", [])

        if not hits:
            _CACHE[cache_key] = copy.deepcopy(result)
            return result

        pageids = [str(hit["pageid"]) for hit in hits]
        detail_resp = session.get(
            API_URL,
            params={
                "action": "query",
                "pageids": "|".join(pageids),
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "exlimit": "max",
                "inprop": "url",
                "format": "json",
            },
            timeout=TIMEOUT_SECONDS,
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


@beta_tool
def search_wikipedia(query: str) -> str:
    """Search English Wikipedia for factual information.

    Call this whenever answering needs facts that can be verified — people,
    places, dates, definitions, events, or "who/what/when/where" questions. Do
    not answer factual questions from prior knowledge; search first.

    Args:
        query: A few keywords or a short phrase to search for.

    Returns:
        A JSON string with the top results, each having title, pageid, url,
        snippet, and an intro extract; an "error" field is set on failure.
    """
    return json.dumps(wikipedia_search(query), ensure_ascii=False)


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
