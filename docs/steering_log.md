# Steering Log

A running log of how the project was steered (decisions, course corrections).

## 2026-06-16 — Skeleton review adjustments

Reviewed the design doc's repo shape before implementation. Three changes:

1. **Renamed `evals/grade_traces.py` → `evals/grade_run.py`.** The original
   `grade_trace.py` / `grade_traces.py` pair differed by one letter, an
   easy-to-misimport trap. `grade_run.py` (grade all traces in one run dir)
   reads clearly against single-trace `grade_trace.py`. Design doc updated to
   match.
2. **Added `evals/__init__.py`** and standardized on `python -m evals.<module>`
   with package-relative imports, so eval modules import each other
   consistently regardless of launch directory.
3. **Added `requirements-dev.txt`** (`pytest`) so the planned `tests/` run on a
   clean venv without polluting runtime dependencies.

Deferred (intentionally not changed): no numpy/scipy in stats (stdlib is
enough for Wilson/bootstrap); `schemas.py` dataclasses-vs-pydantic decision;
prompt registry beyond a single `PROMPT_VERSION`.

## 2026-06-16 — Default model: claude-haiku-4-5 (not Opus)

Changed the `config.py` default model from `claude-opus-4-8` to
`claude-haiku-4-5`. Rationale: the system is designed to ground answers in
Wikipedia retrieval, so we want the model reasoning over retrieved evidence
rather than relying on parametric knowledge. A larger model's broader recall is
a liability for the "trustworthy evidence-seeking" goal (more room to answer
from memory instead of sources) and is not cost-effective for this workload.
The default lives in code (not just the gitignored `.env`) so a fresh clone
inherits the decision. Override with `ANTHROPIC_MODEL` if a task needs more
capability. Note: unlike Opus 4.8, Haiku 4.5 accepts `temperature`.

## 2026-06-16 — Trace capture via tool-boundary instrumentation

Decision: capture traces by **instrumenting the local tool** (record each
call's input, output, and timing as it executes) while keeping the SDK Tool
Runner — *not* by dropping to a manual `messages.create` `tool_use` /
`tool_result` loop.

Rationale: the eval measures what the system knew at answer time and how well it
used that evidence. Hand-managing `tool_use` / `tool_result` would make us an
author of the very interaction we're scoring; passive observation at the tool
boundary keeps the runner's behavior the clean, unmodified surface under test.
The runner's public stream already exposes tool calls (name + input), the final
answer, `stop_reason`, and per-message `usage`; the only gap is tool *results*,
which the instrumented tool captures directly.

This refines the design doc's fallback rule: we prefer instrumentation; the
manual loop stays a last resort, used only if instrumentation proves
insufficient.
