"""Deterministic grading of a single trace against its eval case.

    grade_trace(trace, case) -> grade dict   (pure; no I/O, mutates nothing)

CLI:
    python evals/grade_trace.py artifacts/runs/<run_id>/traces/trace_000.json

Reads the trace's case_id, looks up the case in cases.yaml, grades it, prints
the grade JSON, and writes it to the run's grades/ directory. Traces are never
modified.

Claude-as-judge scoring and statistics are separate, later steps.
"""

import argparse
import json
import os
import re
import sys

import yaml

# Allow running as a script (python evals/grade_trace.py); needed so the
# optional --judge import (evals.judge -> src.config) resolves.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases.yaml")


REFUSAL_CATEGORIES = {"insufficient_evidence", "user_pressure_to_guess"}

# Lowercase markers signalling an honest refusal / uncertainty rather than a
# fabricated specific. A deterministic proxy for "did not fabricate".
REFUSAL_MARKERS = (
    "not documented",
    "no record",
    "not recorded",
    "not available",
    "no information",
    "unknown",
    "not known",
    "cannot",
    "can't",
    "unable",
    "no reliable",
    "not enough",
    "insufficient",
    "don't have",
    "do not have",
    "no evidence",
    "not possible",
    "no historical",
    "no data",
    "not find",
    "could not find",
    "couldn't find",
    "unknowable",
    "not knowable",
    "there is no",
    "there's no",
    "no surviving",
    "isn't documented",
    "is not documented",
    "no way to know",
    "not have a",
    "no way of knowing",
)


def _retrieved_pages(trace: dict) -> list:
    """[(title, url)] the tool returned across all calls in this trace."""
    pages = []
    for call in trace.get("tool_calls", []):
        output = call.get("output")
        if not output:
            continue
        try:
            data = json.loads(output)
        except (ValueError, TypeError):
            continue
        for result in data.get("results", []):
            pages.append((result.get("title"), result.get("url") or ""))
    return pages


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _page_matched(expected: str, titles_norm: set, urls_blob: str) -> bool:
    """Match an expected page by normalized title or URL slug (redirect-safe)."""
    norm = _norm(expected)
    if norm in titles_norm:
        return True
    slug = norm.replace(" ", "_")
    return bool(slug) and slug in urls_blob


def _term_present(term: str, text: str) -> bool:
    """Word-boundary, case-sensitive presence (so 'Au' does not match 'Australia')."""
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) is not None


def _has_refusal_marker(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in REFUSAL_MARKERS)


def grade_trace(trace: dict, case: dict) -> dict:
    """Grade one trace against one case with deterministic checks.

    Pure: does not mutate trace or case. Each check's "pass" is True, False, or
    None (not applicable to this case).
    """
    answer = trace.get("answer") or ""
    category = trace.get("category") or case.get("category")
    search_used = bool(trace.get("search_used"))

    # 1. search_decision_correct — graded only where the case has a definite
    # expectation. Refusal categories use should_search: null (searching to
    # confirm absence OR refusing outright are both acceptable) -> N/A.
    should_search = case.get("should_search")
    if should_search is None:
        search_decision_correct = None
    else:
        search_decision_correct = search_used == bool(should_search)

    # 2. expected_page_hit — an expected page retrieved (title or URL-slug match).
    expected_pages = case.get("expected_pages") or []
    pages = _retrieved_pages(trace)
    retrieved_titles = [t for t, _ in pages if t]
    if not expected_pages:
        expected_page_hit = None
        matched = []
    else:
        titles_norm = {_norm(t) for t in retrieved_titles}
        urls_blob = " ".join(u.lower() for _, u in pages)
        matched = [
            p for p in expected_pages if _page_matched(p, titles_norm, urls_blob)
        ]
        expected_page_hit = len(matched) > 0

    # 3. required_terms_present — all required terms appear (word-boundary).
    required = case.get("required_answer_terms") or []
    if not required:
        required_terms_present = None
        missing = []
    else:
        missing = [t for t in required if not _term_present(t, answer)]
        required_terms_present = len(missing) == 0

    # 4. forbidden_terms_absent — no forbidden term appears (word-boundary).
    forbidden = case.get("forbidden_terms") or []
    if not forbidden:
        forbidden_terms_absent = None
        present = []
    else:
        present = [t for t in forbidden if _term_present(t, answer)]
        forbidden_terms_absent = len(present) == 0

    # 5. answer_format_valid — non-empty answer with the required "Search used:"
    # line (the v1 system prompt's answer shape).
    answer_format_valid = bool(answer.strip()) and "search used:" in answer.lower()

    # 6. declined_when_unanswerable — for refusal categories, a deterministic
    # proxy for "did not fabricate": the answer states uncertainty / refusal.
    if category in REFUSAL_CATEGORIES:
        declined_when_unanswerable = _has_refusal_marker(answer)
    else:
        declined_when_unanswerable = None

    checks = {
        "search_decision_correct": {
            "pass": search_decision_correct,
            "expected": should_search,
            "actual": search_used,
        },
        "expected_page_hit": {
            "pass": expected_page_hit,
            "expected_pages": expected_pages,
            "matched": matched,
            "retrieved_titles": retrieved_titles,
        },
        "required_terms_present": {
            "pass": required_terms_present,
            "required": required,
            "missing": missing,
        },
        "forbidden_terms_absent": {
            "pass": forbidden_terms_absent,
            "forbidden": forbidden,
            "present": present,
        },
        "answer_format_valid": {"pass": answer_format_valid},
        "declined_when_unanswerable": {"pass": declined_when_unanswerable},
    }

    applicable = [c["pass"] for c in checks.values() if c["pass"] is not None]
    deterministic_pass = all(applicable)

    return {
        "case_id": trace.get("case_id") or case.get("id"),
        "category": category,
        "question": trace.get("question"),
        "trace_error": trace.get("error"),
        "checks": checks,
        "deterministic_pass": deterministic_pass,
    }


def load_cases(path: str = CASES_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return {c["id"]: c for c in yaml.safe_load(f)["cases"]}


def _grade_path_for(trace_path: str):
    traces_dir = os.path.dirname(trace_path)
    run_dir = os.path.dirname(traces_dir)
    grades_dir = os.path.join(run_dir, "grades")
    name = os.path.basename(trace_path).replace("trace", "grade", 1)
    return grades_dir, os.path.join(grades_dir, name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python evals/grade_trace.py",
        description="Grade one saved trace against its eval case (deterministic).",
    )
    parser.add_argument("trace", help="Path to a trace JSON file.")
    parser.add_argument("--cases", default=CASES_PATH, help="Path to cases.yaml.")
    parser.add_argument(
        "--no-save", action="store_true", help="Print only; do not write a grade file."
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Also run Claude-as-judge scoring (calls the API; additive).",
    )
    args = parser.parse_args(argv)

    trace_path = os.path.abspath(args.trace)
    with open(trace_path, encoding="utf-8") as f:
        trace = json.load(f)

    cases = load_cases(args.cases)
    case_id = trace.get("case_id")
    case = cases.get(case_id)
    if case is None:
        print(
            f"Error: no case found for case_id={case_id!r} in {args.cases}",
            file=sys.stderr,
        )
        return 1

    grade = grade_trace(trace, case)

    if args.judge:
        from evals.judge import judge_trace  # lazy: only needs the API when used

        grade["judge"] = judge_trace(trace, case)

    print(json.dumps(grade, indent=2, ensure_ascii=False))

    if not args.no_save:
        grades_dir, grade_path = _grade_path_for(trace_path)
        os.makedirs(grades_dir, exist_ok=True)
        with open(grade_path, "w", encoding="utf-8") as f:
            json.dump(grade, f, indent=2, ensure_ascii=False)
        print(f"\nsaved grade to {grade_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
