"""Run the agent over benchmark cases and save one trace per case.

Produces traces under artifacts/runs/<run_id>/ for later grading. Grading is a
separate step and is NOT performed here.

    python evals/run_evals.py
    python evals/run_evals.py --limit 5
    python evals/run_evals.py --category comparison
    python evals/run_evals.py --category comparison --limit 3

Also runs as a module: python -m evals.run_evals --limit 5
"""

import argparse
import os
import sys

import yaml

# Allow running as a script (python evals/run_evals.py), not just as a module.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import config, prompts, trace_store, wikipedia_tool  # noqa: E402
from src.agent import answer_question  # noqa: E402

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases.yaml")


def load_cases(path: str = CASES_PATH) -> list:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["cases"]


def _stratified(cases: list, limit: int) -> list:
    """Pick up to `limit` cases spread across categories (round-robin), so a
    quick subset isn't all one category. Returned in original file order."""
    buckets = {}
    for c in cases:
        buckets.setdefault(c.get("category"), []).append(c)
    order = {id(c): i for i, c in enumerate(cases)}
    picked, depth = [], 0
    while len(picked) < limit and any(depth < len(b) for b in buckets.values()):
        for b in buckets.values():
            if depth < len(b) and len(picked) < limit:
                picked.append(b[depth])
        depth += 1
    return sorted(picked, key=lambda c: order[id(c)])


def select_cases(cases: list, category: str = None, limit: int = None) -> list:
    """Filter by category (if given), then limit. A bare --limit samples across
    categories (stratified); --category --limit takes the first N of that one."""
    selected = cases
    if category:
        selected = [c for c in selected if c.get("category") == category]
    if limit is not None:
        selected = selected[:limit] if category else _stratified(selected, limit)
    return selected


def _failed_record(question: str, error: str) -> dict:
    """A full-shaped trace record for a case that raised before completing.

    Mirrors answer_question's return shape so every trace has a stable schema,
    even when a case fails. Used to isolate per-case failures from the run.
    """
    return {
        "question": question,
        "model": config.MODEL,
        "prompt_version": prompts.PROMPT_VERSION,
        "tool_schema_version": wikipedia_tool.TOOL_SCHEMA_VERSION,
        "answer": "",
        "search_used": False,
        "tool_calls": [],
        "raw_messages": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "latency_s": 0.0,
        "error": error,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python evals/run_evals.py",
        description="Run the agent over benchmark cases and save traces.",
    )
    parser.add_argument("--category", help="Only run cases in this category.")
    parser.add_argument("--limit", type=int, help="Run at most N cases.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config.get_api_key()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    cases = load_cases()
    selected = select_cases(cases, args.category, args.limit)

    if not selected:
        msg = "No cases matched."
        if args.category:
            available = sorted({c.get("category") for c in cases})
            msg = (
                f"No cases in category {args.category!r}. "
                f"Available: {', '.join(available)}"
            )
        print(msg, file=sys.stderr)
        return 1

    run_id = trace_store.start_run()
    scope = f" in category {args.category!r}" if args.category else ""
    print(f"run {run_id}: running {len(selected)} case(s){scope}")

    trace_files = []
    errors = 0
    for i, case in enumerate(selected, 1):
        try:
            record = answer_question(case["question"])
        except Exception as e:  # isolate per-case failures; keep the run going
            record = _failed_record(case["question"], f"{type(e).__name__}: {e}")
        record["case_id"] = case["id"]
        record["category"] = case["category"]
        path = trace_store.save_trace(run_id, record)
        trace_files.append(os.path.basename(path))

        if record["error"]:
            errors += 1
            status = "ERROR"
        else:
            status = "search" if record["search_used"] else "no-search"
        print(f"  [{i}/{len(selected)}] {case['id']:8} {case['category']:30} {status}")

    trace_store.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "model": config.MODEL,
            "prompt_version": prompts.PROMPT_VERSION,
            "tool_schema_version": wikipedia_tool.TOOL_SCHEMA_VERSION,
            "source": "run_evals",
            "category_filter": args.category,
            "limit": args.limit,
            "num_cases": len(selected),
            "traces": trace_files,
        },
    )

    tail = f"  ({errors} error(s))" if errors else ""
    print(f"saved {len(trace_files)} trace(s) to {trace_store.run_dir(run_id)}{tail}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
