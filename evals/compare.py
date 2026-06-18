"""Compare two graded eval runs: paired case-level + aggregate deltas.

    python evals/compare.py <run1> <run2>

For each binary metric it reports both runs' rates, the delta, the paired
discordant counts (improved fail->pass, regressed pass->fail), and a two-sided
exact McNemar p-value. It also diffs run health and per-category pass rates, and
shows judge aggregates side by side when present.

Two-run only (N-run comparison deferred). Use it to A/B prompts/tools, or to
check reproducibility by comparing a config against another run of itself.

Both runs must already be graded (run grade_run first). Reads grades/ and
summary.json from each run; does not modify either run.
"""

import argparse
import glob
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from evals.grade_run import CHECK_NAMES, resolve_run  # noqa: E402
from evals.stats import mcnemar_exact_p  # noqa: E402

METRICS = ["deterministic_pass"] + CHECK_NAMES
COMPARISONS_DIR = os.path.join(_ROOT, "artifacts", "comparisons")


def _load_grades(run_dir: str) -> dict:
    grades = {}
    for path in glob.glob(os.path.join(run_dir, "grades", "*.json")):
        with open(path, encoding="utf-8") as f:
            grade = json.load(f)
        cid = grade.get("case_id")
        if cid:
            grades[cid] = grade
    return grades


def _load_summary(run_dir: str) -> dict:
    path = os.path.join(run_dir, "summary.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _metric_value(grade: dict, metric: str):
    """Per-case outcome (True/False/None) for a metric."""
    if metric == "deterministic_pass":
        return grade.get("deterministic_pass")
    return grade.get("checks", {}).get(metric, {}).get("pass")


def _paired_metric(grades1: dict, grades2: dict, common: list, metric: str) -> dict:
    """Paired comparison of one binary metric over cases applicable in both."""
    p1 = t1 = p2 = t2 = 0
    improved, regressed = [], []  # c: fail->pass, b: pass->fail
    for cid in common:
        v1 = _metric_value(grades1[cid], metric)
        v2 = _metric_value(grades2[cid], metric)
        if v1 is None or v2 is None:
            continue
        t1 += 1
        t2 += 1
        p1 += int(bool(v1))
        p2 += int(bool(v2))
        if v1 and not v2:
            regressed.append(cid)
        elif not v1 and v2:
            improved.append(cid)
    rate1 = p1 / t1 if t1 else None
    rate2 = p2 / t2 if t2 else None
    return {
        "n": t1,
        "rate1": rate1,
        "rate2": rate2,
        "delta": (rate2 - rate1) if (rate1 is not None and rate2 is not None) else None,
        "improved": improved,
        "regressed": regressed,
        "mcnemar_p": mcnemar_exact_p(len(regressed), len(improved)),
    }


def _category_rates(grades: dict, common: list) -> dict:
    cats = {}
    for cid in common:
        g = grades[cid]
        bucket = cats.setdefault(g.get("category"), [0, 0])
        bucket[1] += 1
        if g.get("deterministic_pass"):
            bucket[0] += 1
    return {c: (p, t) for c, (p, t) in cats.items()}


def _pct(x) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def _config(summary: dict) -> dict:
    return {
        "model": summary.get("model"),
        "prompt_version": summary.get("prompt_version"),
        "tool_schema_version": summary.get("tool_schema_version"),
    }


def build_report(run1: str, run2: str, dir1: str, dir2: str) -> str:
    grades1, grades2 = _load_grades(dir1), _load_grades(dir2)
    if not grades1:
        raise SystemExit(f"Run {run1} has no grades — run grade_run first.")
    if not grades2:
        raise SystemExit(f"Run {run2} has no grades — run grade_run first.")

    s1, s2 = _load_summary(dir1), _load_summary(dir2)
    cfg1, cfg2 = _config(s1), _config(s2)

    ids1, ids2 = set(grades1), set(grades2)
    common = sorted(ids1 & ids2)
    only1, only2 = sorted(ids1 - ids2), sorted(ids2 - ids1)

    lines = [f"# Run comparison: {run1} vs {run2}", ""]
    lines.append(f"- run1 `{run1}`: {cfg1}")
    lines.append(f"- run2 `{run2}`: {cfg2}")
    ter1 = ((s1.get("run_health") or {}).get("tool_error_rate")) or 0
    ter2 = ((s2.get("run_health") or {}).get("tool_error_rate")) or 0
    health_differs = abs(ter1 - ter2) > 0.10
    if cfg1 != cfg2:
        diffs = [k for k in cfg1 if cfg1[k] != cfg2[k]]
        lines.append(f"- Config differs in {diffs} -> this is an **A/B comparison**.")
    elif health_differs:
        lines.append(
            f"- Same declared config, BUT run health differs materially "
            f"(tool-error {ter1 * 100:.0f}% vs {ter2 * 100:.0f}%) -> deltas likely "
            f"reflect a tool/data change NOT captured by config; **not** a clean "
            f"reproducibility check."
        )
    else:
        lines.append(
            "- Same config and similar run health -> **reproducibility check** "
            "(deltas are mostly noise)."
        )
    lines.append(
        f"- Cases compared: {len(common)} (run1-only: {len(only1)}, run2-only: {len(only2)})"
    )
    lines.append("")

    # Run health
    h1 = s1.get("run_health") or {}
    h2 = s2.get("run_health") or {}
    lines.append("## Run health")
    lines.append(
        f"- tool_error_rate: {_pct(h1.get('tool_error_rate'))} -> "
        f"{_pct(h2.get('tool_error_rate'))}  "
        f"(reliable: {h1.get('reliable')} -> {h2.get('reliable')})"
    )
    if h1.get("reliable") is False or h2.get("reliable") is False:
        lines.append(
            "- WARNING: a run is flagged unreliable; retrieval/grounding deltas may "
            "reflect data quality, not the change under test."
        )
    lines.append("")

    # Metric deltas table
    lines.append("## Deterministic metrics (paired, cases applicable in both)")
    lines.append("")
    lines.append("| metric | run1 | run2 | delta | improved | regressed | McNemar p |")
    lines.append("|---|---|---|---|---|---|---|")
    paired = {}
    for metric in METRICS:
        m = _paired_metric(grades1, grades2, common, metric)
        paired[metric] = m
        delta = f"{m['delta'] * 100:+.1f} pp" if m["delta"] is not None else "n/a"
        lines.append(
            f"| {metric} | {_pct(m['rate1'])} | {_pct(m['rate2'])} | {delta} | "
            f"{len(m['improved'])} | {len(m['regressed'])} | {m['mcnemar_p']:.3f} |"
        )
    lines.append("")

    # Case-level changes for the headline metric
    dp = paired["deterministic_pass"]
    lines.append("## Deterministic pass: case-level changes")
    lines.append(f"- Improved (fail->pass): {dp['improved'] or 'none'}")
    lines.append(f"- Regressed (pass->fail): {dp['regressed'] or 'none'}")
    lines.append("")

    # Per-category
    lines.append("## Deterministic pass by category")
    lines.append("")
    lines.append("| category | run1 | run2 | delta |")
    lines.append("|---|---|---|---|")
    cats1, cats2 = _category_rates(grades1, common), _category_rates(grades2, common)
    for cat in sorted(set(cats1) | set(cats2)):
        p1, t1 = cats1.get(cat, (0, 0))
        p2, t2 = cats2.get(cat, (0, 0))
        r1 = p1 / t1 if t1 else None
        r2 = p2 / t2 if t2 else None
        delta = (
            f"{(r2 - r1) * 100:+.1f} pp"
            if (r1 is not None and r2 is not None)
            else "n/a"
        )
        lines.append(f"| {cat} | {p1}/{t1} | {p2}/{t2} | {delta} |")
    lines.append("")

    # Judge side-by-side (not paired)
    j1, j2 = s1.get("judge") or {}, s2.get("judge") or {}
    if j1.get("judged") or j2.get("judged"):
        lines.append("## Judge (each run's judged subset — NOT paired)")
        lines.append("")
        lines.append(f"- judged: {j1.get('judged', 0)} vs {j2.get('judged', 0)}")
        lines.append(
            f"- grounded-correct rate: "
            f"{_pct((j1.get('grounded_correct') or {}).get('rate'))} vs "
            f"{_pct((j2.get('grounded_correct') or {}).get('rate'))}"
        )
        lines.append(
            f"- unsupported_claim_rate: {_pct(j1.get('unsupported_claim_rate'))} vs "
            f"{_pct(j2.get('unsupported_claim_rate'))}"
        )
        for name in ("answer_correctness", "groundedness", "query_quality"):
            m1 = (j1.get("metrics") or {}).get(name, {}).get("mean")
            m2 = (j2.get("metrics") or {}).get(name, {}).get("mean")
            lines.append(f"- {name} mean: {m1} vs {m2}")
        lines.append("")

    lines.append("## Caveats")
    lines.append(
        "- Small suite; paired stats cover only cases applicable in BOTH runs. "
        "McNemar p is a directional signal, not a strong significance claim."
    )
    lines.append(
        "- Judge metrics are each run's own judged subset (different cases / sizes) "
        "and are not paired."
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python evals/compare.py",
        description="Compare two graded eval runs (paired deltas + reproducibility).",
    )
    parser.add_argument("run1", help="First run id or directory.")
    parser.add_argument("run2", help="Second run id or directory.")
    args = parser.parse_args(argv)

    dir1, dir2 = resolve_run(args.run1), resolve_run(args.run2)
    if not dir1:
        print(f"Error: run not found: {args.run1}", file=sys.stderr)
        return 1
    if not dir2:
        print(f"Error: run not found: {args.run2}", file=sys.stderr)
        return 1

    id1 = os.path.basename(dir1.rstrip("/\\"))
    id2 = os.path.basename(dir2.rstrip("/\\"))
    report = build_report(id1, id2, dir1, dir2)

    os.makedirs(COMPARISONS_DIR, exist_ok=True)
    out_path = os.path.join(COMPARISONS_DIR, f"{id1}_vs_{id2}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(report)
    print(f"\nsaved comparison to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
