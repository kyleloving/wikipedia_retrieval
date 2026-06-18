"""Tests for evals.grade_trace matching/checks.

Run from the project root: python -m pytest tests/test_grade_trace.py
"""

import json

from evals.grade_trace import (
    _has_refusal_marker,
    _page_matched,
    _term_present,
    grade_trace,
)


def _trace(answer="", search_used=False, results=None, category="factual_lookup"):
    tool_calls = []
    if results is not None:
        tool_calls = [
            {"input": {"query": "q"}, "output": json.dumps({"results": results})}
        ]
    return {
        "case_id": "t1",
        "category": category,
        "question": "Q?",
        "answer": answer,
        "search_used": search_used,
        "tool_calls": tool_calls,
        "error": None,
    }


def _case(**kw):
    base = {
        "id": "t1",
        "category": "factual_lookup",
        "should_search": True,
        "expected_pages": [],
        "required_answer_terms": [],
        "forbidden_terms": [],
    }
    base.update(kw)
    return base


# --- word-boundary term matching (#4) ---


def test_term_present_word_boundary():
    assert _term_present("Au", "The symbol is Au.") is True
    assert _term_present("Au", "Gold is mined in Australia") is False
    assert _term_present("Au", "the author wrote") is False
    assert _term_present("yen", "the Japanese yen") is True


def test_required_terms_uses_word_boundary():
    g = grade_trace(
        _trace(answer="Gold is common in Australia. Search used: no"),
        _case(required_answer_terms=["Au"]),
    )
    assert (
        g["checks"]["required_terms_present"]["pass"] is False
    )  # 'Au' only inside 'Australia'

    g2 = grade_trace(
        _trace(answer="The symbol is Au. Search used: no"),
        _case(required_answer_terms=["Au"]),
    )
    assert g2["checks"]["required_terms_present"]["pass"] is True


# --- redirect-safe page matching (#5) ---


def test_page_matched_by_title_and_url():
    titles = {"mona lisa"}
    assert _page_matched("Mona Lisa", titles, "") is True
    # title differs but URL slug matches (redirect case)
    assert (
        _page_matched("Mona Lisa", set(), "https://en.wikipedia.org/wiki/mona_lisa")
        is True
    )
    assert (
        _page_matched("Jupiter", {"saturn"}, "https://en.wikipedia.org/wiki/saturn")
        is False
    )


def test_expected_page_hit_via_url_slug():
    results = [
        {
            "title": "Mona Lisa (painting)",
            "url": "https://en.wikipedia.org/wiki/Mona_Lisa",
        }
    ]
    g = grade_trace(
        _trace(answer="...", search_used=True, results=results),
        _case(expected_pages=["Mona Lisa"]),
    )
    assert g["checks"]["expected_page_hit"]["pass"] is True


# --- should_search: null -> N/A (#1) ---


def test_search_decision_na_when_should_search_null():
    g = grade_trace(
        _trace(answer="No record. Search used: no", category="insufficient_evidence"),
        _case(category="insufficient_evidence", should_search=None),
    )
    assert g["checks"]["search_decision_correct"]["pass"] is None


def test_search_decision_graded_when_defined():
    g = grade_trace(
        _trace(answer="x Search used: yes", search_used=True), _case(should_search=True)
    )
    assert g["checks"]["search_decision_correct"]["pass"] is True


# --- refusal marker check for refusal categories (#3) ---


def test_declined_when_unanswerable_refusal_category():
    refused = grade_trace(
        _trace(
            answer="There is no record of that. Search used: no",
            category="insufficient_evidence",
        ),
        _case(category="insufficient_evidence", should_search=None),
    )
    assert refused["checks"]["declined_when_unanswerable"]["pass"] is True

    fabricated = grade_trace(
        _trace(
            answer="Her favorite breakfast was porridge. Search used: no",
            category="insufficient_evidence",
        ),
        _case(category="insufficient_evidence", should_search=None),
    )
    assert fabricated["checks"]["declined_when_unanswerable"]["pass"] is False


def test_declined_check_na_for_non_refusal_category():
    g = grade_trace(
        _trace(answer="Paris. Search used: yes", category="factual_lookup"),
        _case(category="factual_lookup"),
    )
    assert g["checks"]["declined_when_unanswerable"]["pass"] is None


def test_has_refusal_marker():
    assert _has_refusal_marker("This is not documented anywhere.") is True
    assert _has_refusal_marker("The capital is Paris.") is False


# --- require-all page groups (#2): comparisons need BOTH sides ---


def _pages(*titles):
    return [
        {"title": t, "url": f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}"}
        for t in titles
    ]


def test_page_groups_require_every_group():
    case = _case(
        expected_pages=["Nile", "Mississippi River"],
        required_page_groups=[["Nile"], ["Mississippi River"]],
    )
    both = grade_trace(
        _trace(search_used=True, results=_pages("Nile", "Mississippi River")), case
    )
    assert both["checks"]["expected_page_hit"]["pass"] is True

    one = grade_trace(_trace(search_used=True, results=_pages("Nile")), case)
    assert one["checks"]["expected_page_hit"]["pass"] is False  # only one side
    assert one["checks"]["expected_page_hit"]["missing_groups"] == [
        ["Mississippi River"]
    ]


def test_min_distinct_pages_for_ambiguity():
    case = _case(
        category="ambiguous_entity",
        expected_pages=["Java", "Java (programming language)"],
        min_distinct_pages=2,
    )
    two = grade_trace(
        _trace(search_used=True, results=_pages("Java", "Java (programming language)")),
        case,
    )
    assert two["checks"]["expected_page_hit"]["pass"] is True

    one = grade_trace(_trace(search_used=True, results=_pages("Java")), case)
    assert one["checks"]["expected_page_hit"]["pass"] is False  # only one sense


# --- deterministic source grounding (#3): no fabricated citations ---


def test_cited_sources_must_be_retrieved():
    results = _pages("Jupiter")
    grounded = grade_trace(
        _trace(
            answer="Jupiter is largest.\n\nSources used:\n- Jupiter\nSearch used: yes",
            search_used=True,
            results=results,
        ),
        _case(expected_pages=["Jupiter"]),
    )
    assert grounded["checks"]["cited_sources_retrieved"]["pass"] is True

    fabricated = grade_trace(
        _trace(
            answer="Jupiter is largest.\n\nSources used:\n- Saturn (planet)\nSearch used: yes",
            search_used=True,
            results=results,
        ),
        _case(expected_pages=["Jupiter"]),
    )
    assert fabricated["checks"]["cited_sources_retrieved"]["pass"] is False
    assert (
        "Saturn (planet)"
        in fabricated["checks"]["cited_sources_retrieved"]["unretrieved"]
    )


def test_cited_sources_na_when_none_or_no_search():
    none_cited = grade_trace(
        _trace(
            answer="A haiku.\n\nSources used: none\nSearch used: no",
            category="no_search",
        ),
        _case(category="no_search", should_search=False),
    )
    assert none_cited["checks"]["cited_sources_retrieved"]["pass"] is None


def test_cited_sources_tolerates_disambiguator():
    # Cited 'Mona Lisa' should match retrieved 'Mona Lisa (painting)'.
    g = grade_trace(
        _trace(
            answer="Leonardo painted it.\n\nSources used:\n- Mona Lisa\nSearch used: yes",
            search_used=True,
            results=_pages("Mona Lisa (painting)"),
        ),
        _case(expected_pages=["Mona Lisa"]),
    )
    assert g["checks"]["cited_sources_retrieved"]["pass"] is True
