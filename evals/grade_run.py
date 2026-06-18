"""Grade every trace in an eval run; write grades/, summary.json, failures.md.

    python evals/grade_run.py                    # grade the latest run
    python evals/grade_run.py <run_id>           # grade a run by id
    python evals/grade_run.py <path/to/run_dir>  # grade a run by path

Reuses the deterministic checks from grade_trace. Does not modify traces.
Claude-as-judge scoring and confidence intervals are separate, later steps.

Also runs as a module: python -m evals.grade_run
"""

import argparse
import csv
import glob
import json
import os
import sys

# Allow running as a script (python evals/grade_run.py), not just as a module.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from evals.grade_trace import grade_trace, load_cases  # noqa: E402
from evals.stats import wilson_interval  # noqa: E402

RUNS_DIR = os.path.join(_ROOT, "artifacts", "runs")

CHECK_NAMES = [
    "search_decision_correct",
    "expected_page_hit",
    "required_terms_present",
    "forbidden_terms_absent",
    "answer_format_valid",
    "declined_when_unanswerable",
    "cited_sources_retrieved",
]

# Judge metrics aggregated when present (grade_trace --judge). 0/1/2 scores.
JUDGE_ORDINAL = [
    "answer_correctness",
    "groundedness",
    "query_quality",
    "answer_usefulness",
]
JUDGE_NULLABLE = ["ambiguity_handling", "insufficient_evidence_handling"]

# Above this fraction of tool calls failing, expected_page_hit / groundedness
# are unreliable and the run should be re-run (e.g. rate limiting).
TOOL_ERROR_WARN_THRESHOLD = 0.10


def resolve_run(arg: str = None) -> str:
    """Resolve a run id / path / (default) latest run to a run directory."""
    if arg:
        if os.path.isdir(arg):
            return os.path.abspath(arg)
        candidate = os.path.join(RUNS_DIR, arg)
        return candidate if os.path.isdir(candidate) else None
    dirs = [d for d in glob.glob(os.path.join(RUNS_DIR, "*")) if os.path.isdir(d)]
    return max(dirs) if dirs else None


def _trace_tool_health(trace: dict, health: dict) -> None:
    """Accumulate tool-call health counters from one trace (in place)."""
    health["traces"] += 1
    if trace.get("error"):
        health["traces_with_trace_error"] += 1
    had_tool_error = False
    for call in trace.get("tool_calls", []):
        health["tool_calls"] += 1
        output = call.get("output")
        try:
            data = json.loads(output) if output else {}
        except (ValueError, TypeError):
            data = {}
        err = data.get("error")
        if err:
            health["tool_calls_errored"] += 1
            had_tool_error = True
            if "429" in str(err):
                health["rate_limited_calls"] += 1
    if had_tool_error:
        health["traces_with_tool_error"] += 1


def _judge_with_retry(trace: dict, case: dict, attempts: int = 3):
    """Judge one trace, retrying transient failures. Raises on final failure."""
    from evals.judge import judge_trace

    last = None
    for _ in range(attempts):
        try:
            return judge_trace(trace, case)
        except Exception as e:  # transient API errors; retry
            last = e
    raise last


def _prior_judge(grade_path: str):
    if not os.path.exists(grade_path):
        return None
    try:
        with open(grade_path, encoding="utf-8") as f:
            return json.load(f).get("judge")
    except (ValueError, OSError):
        return None


def grade_run(run_dir: str, cases: dict, judge: bool = False, judge_limit: int = None):
    """Grade every trace in run_dir, writing grades/grade_NNN.json.

    Deterministic by default. With judge=True, runs Claude-as-judge on each trace
    (up to judge_limit, with retry). Without it, an existing judge block is
    preserved so a deterministic re-grade does not discard prior judge scores.

    Returns (grades, skipped, health).
    """
    grades_dir = os.path.join(run_dir, "grades")
    os.makedirs(grades_dir, exist_ok=True)

    health = {
        "traces": 0,
        "traces_with_trace_error": 0,
        "traces_with_tool_error": 0,
        "tool_calls": 0,
        "tool_calls_errored": 0,
        "rate_limited_calls": 0,
    }
    grades, skipped = [], []
    judged = 0
    for trace_path in sorted(glob.glob(os.path.join(run_dir, "traces", "*.json"))):
        with open(trace_path, encoding="utf-8") as f:
            trace = json.load(f)
        _trace_tool_health(trace, health)

        case = cases.get(trace.get("case_id"))
        if case is None:
            skipped.append(os.path.basename(trace_path))
            continue
        grade = grade_trace(trace, case)

        name = os.path.basename(trace_path).replace("trace", "grade", 1)
        grade_path = os.path.join(grades_dir, name)

        judge_block = None
        if judge and (judge_limit is None or judged < judge_limit):
            try:
                judge_block = _judge_with_retry(trace, case)
                judged += 1
                print(f"  judged {trace.get('case_id')}", file=sys.stderr)
            except Exception as e:
                print(
                    f"  judge FAILED for {trace.get('case_id')}: {e}", file=sys.stderr
                )
        if judge_block is None:
            judge_block = _prior_judge(grade_path)  # preserve any prior judge
        if judge_block is not None:
            grade["judge"] = judge_block

        grades.append(grade)
        with open(grade_path, "w", encoding="utf-8") as f:
            json.dump(grade, f, indent=2, ensure_ascii=False)
    return grades, skipped, health


def _rate(passed: int, applicable: int):
    return round(passed / applicable, 3) if applicable else None


def _ci(passed: int, applicable: int):
    """Wilson 95% CI as [low, high] rounded, or None when not applicable."""
    interval = wilson_interval(passed, applicable)
    return [round(interval[0], 3), round(interval[1], 3)] if interval else None


def _aggregate_judge(grades: list) -> dict:
    """Aggregate judge scores over the grades that carry a judge block."""
    judged = [g["judge"] for g in grades if g.get("judge")]
    if not judged:
        return {"judged": 0}

    metrics = {}
    for name in JUDGE_ORDINAL + JUDGE_NULLABLE:
        values = [j[name] for j in judged if j.get(name) is not None]
        dist = {"0": 0, "1": 0, "2": 0}
        for v in values:
            dist[str(v)] += 1
        metrics[name] = {
            "n": len(values),
            "dist": dist,
            "mean": round(sum(values) / len(values), 3) if values else None,
        }

    total_claims = sum(j.get("factual_claim_count", 0) or 0 for j in judged)
    unsupported = sum(j.get("unsupported_claim_count", 0) or 0 for j in judged)
    grounded_correct = sum(
        1
        for j in judged
        if j.get("answer_correctness") == 2 and j.get("groundedness") == 2
    )

    return {
        "judged": len(judged),
        "metrics": metrics,
        "factual_claim_count": total_claims,
        "unsupported_claim_count": unsupported,
        "unsupported_claim_rate": (
            round(unsupported / total_claims, 3) if total_claims else None
        ),
        "grounded_correct": {
            "count": grounded_correct,
            "judged": len(judged),
            "rate": _rate(grounded_correct, len(judged)),
        },
    }


def _run_health(health: dict) -> dict:
    calls = health["tool_calls"]
    out = dict(health)
    out["tool_error_rate"] = (
        round(health["tool_calls_errored"] / calls, 3) if calls else None
    )
    out["reliable"] = (out["tool_error_rate"] or 0) <= TOOL_ERROR_WARN_THRESHOLD
    return out


def _pct(x) -> str:
    return f"{x * 100:.0f}%" if x is not None else "n/a"


def _executive_summary(summary: dict, grades: list) -> dict:
    """A deterministic, human-readable synthesis of the run's stats + failures."""
    dp = summary["deterministic_pass"]
    ci = dp["ci_95"]
    ci_s = f", 95% CI {_pct(ci[0])}-{_pct(ci[1])}" if ci else ""
    headline = (
        f"{dp['passed']}/{dp['total']} cases passed all applicable deterministic "
        f"checks ({_pct(dp['rate'])}{ci_s})."
    )

    h = summary["run_health"]
    if h["reliable"]:
        reliability = (
            f"Run looks reliable ({_pct(h['tool_error_rate'])} tool-call failures)."
        )
    else:
        reliability = (
            f"UNRELIABLE: {_pct(h['tool_error_rate'])} of tool calls failed "
            f"({h['rate_limited_calls']} rate-limited). Retrieval-dependent metrics "
            f"(expected_page_hit, groundedness) are not trustworthy — re-run first."
        )

    findings = []
    rated = [
        (n, summary["checks"][n]["rate"], summary["checks"][n]["applicable"])
        for n in CHECK_NAMES
        if summary["checks"][n]["rate"] is not None
    ]
    # Prefer checks with a real sample (>=5) so a 100% on n=1 isn't a "headline".
    meaningful = [r for r in rated if r[2] >= 5] or rated
    if meaningful:
        strongest = max(meaningful, key=lambda x: (x[1], x[2]))
        weakest = min(meaningful, key=lambda x: (x[1], -x[2]))
        findings.append(
            f"Strongest check: {strongest[0]} ({_pct(strongest[1])}, n={strongest[2]})."
        )
        findings.append(
            f"Weakest check: {weakest[0]} ({_pct(weakest[1])}, n={weakest[2]})."
        )

    fail_counts = {
        n: sum(1 for g in grades if g["checks"][n]["pass"] is False)
        for n in CHECK_NAMES
    }
    top_check, top_n = max(fail_counts.items(), key=lambda x: x[1])
    if top_n:
        findings.append(f"Most common failure: {top_check} ({top_n} cases).")

    weak_cats = sorted(
        (
            (c, b["rate"], b["deterministic_pass"], b["total"])
            for c, b in summary["by_category"].items()
            if b["rate"] is not None
        ),
        key=lambda x: x[1],
    )
    named = [f"{c} ({p}/{t})" for c, r, p, t in weak_cats[:3] if r < 0.5]
    if named:
        findings.append("Weakest categories: " + ", ".join(named) + ".")

    j = summary["judge"]
    if j.get("judged"):
        findings.append(
            f"Judge (n={j['judged']}): grounded-correct "
            f"{_pct(j['grounded_correct']['rate'])}; "
            f"{_pct(j['unsupported_claim_rate'])} of factual claims unsupported "
            "by retrieved evidence."
        )

    caveats = [
        f"Small suite (n={dp['total']}; ~3-8 per category) — intervals are wide; "
        "treat cross-category differences as directional, not significant."
    ]
    if not h["reliable"]:
        caveats.append(
            "This run is flagged unreliable by run_health; fix data quality and "
            "re-run before drawing conclusions about retrieval/grounding."
        )
    if j.get("judged") and j["judged"] < dp["total"]:
        caveats.append(
            f"Judge metrics cover only {j['judged']} of {dp['total']} cases — directional only."
        )

    return {
        "headline": headline,
        "reliability": reliability,
        "findings": findings,
        "caveats": caveats,
    }


def summarize(grades: list, run_dir: str, manifest: dict, health: dict) -> dict:
    total = len(grades)
    det_pass = sum(1 for g in grades if g["deterministic_pass"])

    checks = {}
    for name in CHECK_NAMES:
        passed = sum(1 for g in grades if g["checks"][name]["pass"] is True)
        failed = sum(1 for g in grades if g["checks"][name]["pass"] is False)
        applicable = passed + failed
        checks[name] = {
            "passed": passed,
            "failed": failed,
            "applicable": applicable,
            "na": total - applicable,
            "rate": _rate(passed, applicable),
            "ci_95": _ci(passed, applicable),
        }

    by_category = {}
    for g in grades:
        bucket = by_category.setdefault(
            g["category"], {"total": 0, "deterministic_pass": 0}
        )
        bucket["total"] += 1
        if g["deterministic_pass"]:
            bucket["deterministic_pass"] += 1
    for bucket in by_category.values():
        bucket["rate"] = _rate(bucket["deterministic_pass"], bucket["total"])
        bucket["ci_95"] = _ci(bucket["deterministic_pass"], bucket["total"])

    base = {
        "run_id": os.path.basename(run_dir.rstrip("/\\")),
        "model": manifest.get("model"),
        "prompt_version": manifest.get("prompt_version"),
        "tool_schema_version": manifest.get("tool_schema_version"),
        "total_cases": total,
        "run_health": _run_health(health),
        "deterministic_pass": {
            "passed": det_pass,
            "total": total,
            "rate": _rate(det_pass, total),
            "ci_95": _ci(det_pass, total),
        },
        "checks": checks,
        "by_category": by_category,
        "judge": _aggregate_judge(grades),
    }

    # Place the synthesized executive summary near the top of the file.
    head_keys = (
        "run_id",
        "model",
        "prompt_version",
        "tool_schema_version",
        "total_cases",
    )
    ordered = {k: base[k] for k in head_keys}
    ordered["executive_summary"] = _executive_summary(base, grades)
    for k, v in base.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def _fail_detail(name: str, check: dict) -> str:
    if name == "search_decision_correct":
        return f" (expected search={check['expected']}, got {check['actual']})"
    if name == "expected_page_hit":
        if check.get("missing_groups"):
            return f" (groups not retrieved: {check['missing_groups']})"
        return f" (none of {check['expected_pages']} retrieved)"
    if name == "required_terms_present":
        return f" (missing {check['missing']})"
    if name == "forbidden_terms_absent":
        return f" (present {check['present']})"
    if name == "answer_format_valid":
        return " (missing 'Search used:' line or empty answer)"
    if name == "declined_when_unanswerable":
        return " (no refusal/uncertainty marker — may have fabricated)"
    if name == "cited_sources_retrieved":
        return f" (cited but never retrieved: {check.get('unretrieved')})"
    return ""


def write_failures_md(grades: list, path: str) -> int:
    failing = [g for g in grades if not g["deterministic_pass"] or g.get("trace_error")]
    lines = [
        "# Failures",
        "",
        f"{len(failing)} of {len(grades)} cases failed at least one deterministic "
        "check (or errored). Grouped by category.",
        "",
    ]
    by_category = {}
    for g in failing:
        by_category.setdefault(g["category"], []).append(g)

    for category in sorted(by_category):
        group = by_category[category]
        lines.append(f"## {category} ({len(group)})")
        lines.append("")
        for g in group:
            lines.append(f"- **{g['case_id']}** — {g['question']}")
            if g.get("trace_error"):
                lines.append(f"  - trace error: `{g['trace_error']}`")
            for name in CHECK_NAMES:
                check = g["checks"][name]
                if check["pass"] is False:
                    lines.append(f"  - FAIL `{name}`{_fail_detail(name, check)}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return len(failing)


def _load_manifest(run_dir: str) -> dict:
    path = os.path.join(run_dir, "manifest.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _fmt_ci(ci) -> str:
    return f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]" if ci else "n/a"


def write_summary_csv(summary: dict, path: str) -> None:
    """Flat one-row-per-metric CSV with rate and Wilson 95% CI."""
    rows = []
    dp = summary["deterministic_pass"]
    ci = dp["ci_95"] or [None, None]
    rows.append(
        ["deterministic_pass", dp["passed"], dp["total"], dp["rate"], ci[0], ci[1]]
    )
    for name in CHECK_NAMES:
        c = summary["checks"][name]
        ci = c["ci_95"] or [None, None]
        rows.append([name, c["passed"], c["applicable"], c["rate"], ci[0], ci[1]])
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "passed", "total", "rate", "ci_low", "ci_high"])
        writer.writerows(rows)


def _print_summary(summary: dict, n_failures: int, skipped: list) -> None:
    print(f"Run {summary['run_id']}  (model {summary['model']})")
    dp = summary["deterministic_pass"]
    pct = f"{dp['rate'] * 100:.1f}%" if dp["rate"] is not None else "n/a"
    print(
        f"Deterministic pass: {dp['passed']}/{dp['total']}  ({pct})  "
        f"95% CI {_fmt_ci(dp['ci_95'])}"
    )
    if skipped:
        print(f"Skipped (no matching case): {len(skipped)}")

    h = summary["run_health"]
    her = (
        f"{h['tool_error_rate'] * 100:.1f}%"
        if h["tool_error_rate"] is not None
        else "n/a"
    )
    print(
        f"Run health: {h['tool_calls_errored']}/{h['tool_calls']} tool calls failed "
        f"({her}), {h['rate_limited_calls']} rate-limited, "
        f"{h['traces_with_tool_error']} traces affected"
    )
    if not h["reliable"]:
        print(
            "  WARNING: high tool-failure rate — expected_page_hit / groundedness "
            "are unreliable; re-run before trusting them."
        )
    print()

    print(f"{'check':28} {'pass':>5} {'appl':>5} {'rate':>7}  {'95% CI':>16}")
    for name in CHECK_NAMES:
        c = summary["checks"][name]
        rate = f"{c['rate'] * 100:.1f}%" if c["rate"] is not None else "n/a"
        print(
            f"{name:28} {c['passed']:>5} {c['applicable']:>5} {rate:>7}  "
            f"{_fmt_ci(c['ci_95']):>16}"
        )
    print()

    print(f"By category ({'pass/total':>10}  {'95% CI':>16}):")
    for cat in sorted(summary["by_category"]):
        b = summary["by_category"][cat]
        pt = f"{b['deterministic_pass']}/{b['total']}"
        print(f"  {cat:30} {pt:>10}  {_fmt_ci(b['ci_95']):>16}")
    print()

    j = summary["judge"]
    if j["judged"]:
        print(f"Judge (n={j['judged']} judged):")
        for name in JUDGE_ORDINAL + JUDGE_NULLABLE:
            m = j["metrics"][name]
            mean = f"{m['mean']:.2f}" if m["mean"] is not None else "n/a"
            d = m["dist"]
            print(
                f"  {name:32} mean {mean:>4} (n={m['n']:>2})  "
                f"0/1/2 = {d['0']}/{d['1']}/{d['2']}"
            )
        gc = j["grounded_correct"]
        gcr = f"{gc['rate'] * 100:.1f}%" if gc["rate"] is not None else "n/a"
        ucr = (
            f"{j['unsupported_claim_rate'] * 100:.1f}%"
            if j["unsupported_claim_rate"] is not None
            else "n/a"
        )
        print(f"  grounded-correct (both=2): {gc['count']}/{gc['judged']} ({gcr})")
        print(
            f"  unsupported claims: {j['unsupported_claim_count']}/"
            f"{j['factual_claim_count']} ({ucr})"
        )
        print()

    print(f"Failures: {n_failures}  -> see failures.md")

    es = summary["executive_summary"]
    print()
    print("Executive summary:")
    print(f"  {es['headline']}")
    print(f"  {es['reliability']}")
    for finding in es["findings"]:
        print(f"  - {finding}")
    for caveat in es["caveats"]:
        print(f"  ! {caveat}")
    if es.get("narrative"):
        print()
        print("LLM narrative:")
        print(es["narrative"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python evals/grade_run.py",
        description="Grade every trace in an eval run (deterministic).",
    )
    parser.add_argument(
        "run", nargs="?", help="Run id or run directory (default: latest run)."
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Run Claude-as-judge on the run's traces (calls the API, with retry).",
    )
    parser.add_argument(
        "--judge-limit",
        type=int,
        default=None,
        help="Cap how many traces are judged (default: all).",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Add an LLM narrative to the executive summary (calls the API).",
    )
    args = parser.parse_args(argv)

    run_dir = resolve_run(args.run)
    if not run_dir:
        print(f"Error: no run found ({args.run or 'latest'}).", file=sys.stderr)
        return 1

    cases = load_cases()
    grades, skipped, health = grade_run(
        run_dir, cases, judge=args.judge, judge_limit=args.judge_limit
    )
    if not grades:
        print(f"Error: no gradable traces in {run_dir}.", file=sys.stderr)
        return 1

    manifest = _load_manifest(run_dir)
    summary = summarize(grades, run_dir, manifest, health)

    if args.summary:
        from evals.judge import executive_narrative  # lazy: only needs API here

        try:
            summary["executive_summary"]["narrative"] = executive_narrative(
                summary, grades
            )
        except Exception as e:  # additive — never let it crash the run
            print(f"  (narrative generation failed: {e})", file=sys.stderr)

    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    write_summary_csv(summary, os.path.join(run_dir, "summary.csv"))
    n_failures = write_failures_md(grades, os.path.join(run_dir, "failures.md"))

    _print_summary(summary, n_failures, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
