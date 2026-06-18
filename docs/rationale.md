# Design Rationale

Concise rationale for the Claude + Wikipedia QA agent and its eval suite. See
[design.md](design.md) for the full design and [steering_log.md](steering_log.md)
for the decision log.

## Model used

- **Agent: `claude-haiku-4-5`** (default; overridable via `ANTHROPIC_MODEL`).
  Chosen deliberately *small*: factual grounding is supposed to come from the
  Wikipedia tool, not the model's parametric knowledge, so a larger model's
  broad recall is a liability (it can answer from memory instead of evidence)
  and a cost. Keeping the agent small makes the prompt and the retrieval tool
  the load-bearing parts — which is what we want to evaluate.
- **Judge: `claude-opus-4-8`** (`ANTHROPIC_JUDGE_MODEL`). The judge is a separate
  role: a small model grading itself is biased and weaker at nuanced calls, so
  the judge is a stronger, independent model.

## Architecture

```
question → CLI (src/app.py)
         → agent (src/agent.py): client.beta.messages.tool_runner
         → local search_wikipedia tool (src/wikipedia_tool.py)
         → English Wikipedia MediaWiki API (HTTP, throttled + retried)
         → Claude final answer
         → trace saved (src/trace_store.py)
         → graded later (evals/): deterministic + optional judge → stats
```

Traces and grades are stored separately so traces can be re-graded without
re-running Claude. The agent stays small; the eval layer is where most of the
work (and most of the value) lives.

## Why the SDK Tool Runner

We use `client.beta.messages.tool_runner` rather than a hand-rolled
`tool_use`/`tool_result` loop. It is the idiomatic SDK path (less boilerplate,
correct loop handling) while preserving the important boundary: Wikipedia
retrieval is *local project code*, not a hosted tool.

Trace capture is done by **instrumenting the tool boundary** (the tool records
its own input/output/latency), not by manipulating the message loop. Rationale:
the eval measures *what the system knew at answer time and how it used it*;
hand-managing the loop would make us an author of the interaction we're scoring,
so we observe passively and keep the runner's behavior the unmodified surface
under test. (A manual loop remains the documented fallback only if instrumentation
proves insufficient.)

## Why hosted search was not used

Hosted `web_search`/RAG was excluded by design (and assignment constraint). We
want Wikipedia retrieval to be *our* code so that: retrieval is inspectable and
reproducible; the model-facing tool boundary is explicit (and thus traceable and
gradeable); and "grounding" can be measured against the exact evidence the tool
returned. A hosted search would hide the retrieval step we most want to evaluate.

## Prompt design

The agent uses a **v1 system prompt** (`src/prompts.py`, wired via `system=` in
`src/agent.py`) that encodes the design's intended behavior: when to search vs.
answer directly; targeted queries and re-searching when results are weak;
retrieve both items for comparisons; ground every factual claim in retrieved
evidence (don't answer factual questions from memory); refuse honestly when
evidence is insufficient or the question is unanswerable; handle ambiguity
explicitly; and end with the structured `Sources used:` / `Search used: yes/no`
shape.

Because the answering model is deliberately small, the prompt and tool — not
parametric knowledge — are meant to do the work. We don't just assert the prompt
helps: the v0 (no system prompt, tool docstring only) → v1 A/B in Results is how
we check that it actually moves the behavioral metrics.

## Eval design

- **50 cases across 10 categories** (`evals/cases.yaml`): factual, multi-hop,
  comparison, ambiguous entity, insufficient evidence, no-search, typo/paraphrase,
  user-pressure-to-guess, source-boundary, distractor. Authored to test
  *behavior* (search decisions, grounding, honest refusal, ambiguity handling),
  not just trivia, with stable facts that appear in Wikipedia lead extracts.
- **Deterministic checks** (always, free): `search_decision_correct` (N/A for
  refusal categories — searching to confirm absence or refusing outright are both
  acceptable), `expected_page_hit` (title- or URL-slug match, redirect-safe),
  `required_terms_present` (word-boundary, so `Au` ≠ `Australia`),
  `forbidden_terms_absent`, `answer_format_valid`, and `declined_when_unanswerable`
  (a deterministic fabrication proxy for the refusal categories); aggregated with
  **Wilson 95% CIs**.
- **Run health**: tool-error rate flags runs degraded by rate limiting, so a
  contaminated run can't be mistaken for genuine agent behavior.
- **Claude-as-judge**: scores correctness and **groundedness independently**,
  plus query quality, ambiguity / insufficient-evidence handling, usefulness, and
  an unsupported-claim rate. Opt-in per trace (`grade_trace --judge`) or across a
  whole run (`grade_run --judge`, with retry).
- **Comparison** (`evals/compare.py`): paired case-level diffs + McNemar for A/B
  and reproducibility.

## Results

**Current run — v1 prompt, clean (`20260617T233035Z`, n=50; this is the committed
`artifacts/sample_run`):**

- **Deterministic pass: 41/50 = 82%** (Wilson 95% CI 69–90%); run health reliable
  (0 tool errors).
- `search_decision_correct` 97.5% (39/40 applicable), `expected_page_hit` 81%,
  `answer_format_valid` 96%, `declined_when_unanswerable` 10/10,
  `required_terms_present` 100%.
- **Judge across all 50 cases:** answer_correctness mean 1.90, **groundedness
  mean 1.68**, query_quality 1.80, usefulness 1.94; **grounded-correct 70%**
  (35/50); **unsupported-claim rate 20.4%** (57/280);
  insufficient_evidence_handling 2.0 across all 13 applicable.

**Prompt impact — A/B vs the no-prompt v0 baseline (`20260617T230550Z`):** adding
the system prompt improved 41 cases with **0 regressions**. `answer_format_valid`
0% → 96% (the structured shape is now emitted), `declined_when_unanswerable`
90% → 100%, `expected_page_hit` 74% → 81%, `search_decision_correct`
92.5% → 97.5%.

**Tooling validation (earlier rate-limit fix):** the first seed run was
contaminated by Wikipedia 429s (74% of tool calls failed). After throttle +
backoff, tool-error rate went 74% → 0%, and `compare.py` surfaced the recovery
directly — which is what first exposed the "correct but ungrounded" pattern.
(Grounding figures above are the full n=50 judge; they supersede an earlier n=6
spot check.)

## Failure modes

- **Query → list/meta pages.** Some factual queries ("tallest mountain",
  "largest planet") retrieve *List of…* pages instead of the target article, so
  `expected_page_hit` still misses on a few cases even when the answer is right.
- **Correct but ungrounded.** ~20% of factual claims (full n=50 judge) still
  aren't supported by the retrieved evidence — the small model leans on memory
  even with the v1 prompt. This is exactly what the small-model + groundedness
  metric are meant to expose; narrowing it further is future prompt work.
- **Ambiguity handling is uneven** (judge ambiguity_handling mean 1.0, n=6): the
  agent disambiguates some entities and answers a single sense for others — a
  quality only the judge sees, not the deterministic checks.
- **`user_pressure_to_guess` is the weakest category** (1/3 deterministic) — the
  hardest "refuse under pressure" cases.

## Iterations

Built incrementally, each step verified before the next: skeleton → basic Claude
call → local Wikipedia tool → tool registration via Tool Runner → CLI (with rich
rendering) → trace capture → 50-case benchmark → deterministic grader →
full-run grading + summary/failures → Wilson CIs → Claude-as-judge → run-health +
judge aggregation + per-category CIs → executive summary (deterministic + LLM) →
rate-limit fix (throttle + backoff) → run comparison → **v1 system prompt** +
rubric calibration (refusal categories N/A for search, word-boundary terms,
redirect-safe page match, refusal/fabrication check) + **full-suite batch judge**,
measured via the v0→v1 A/B. Decisions and course corrections are logged in
`steering_log.md`.

## Extensions (next steps)

- **Prompt v2**: target the residual ~20% ungrounded claims and the weaker
  categories (`user_pressure_to_guess`, ambiguity); re-measure via the A/B.
- **Stronger IR metrics**: recall of *all* expected pages, rank/MRR, an
  evidence-in-extract grounding proxy, `source_titles_valid` (now that v1 emits
  citations), `tool_call_count`.
- **Validate the judge**: test–retest variance and a small human-agreement check.
- **More cases** and N-run comparison.

## Approximate time spent

Built as an AI-assisted, incremental session (the ~20 steps above), each with
explicit verification rather than measured in wall-clock hours. The bulk of the
effort went into the eval layer (benchmark authoring, grading, stats, judge,
comparison) rather than the agent itself, which is intentionally small.

## Known limitation of the tooling

`compare.py` infers "A/B vs reproducibility" from declared config fields
(`model` / `prompt_version` / `tool_schema_version`). The rate-limit fix changed
internal tool *behavior* without changing the tool *schema*, so the
contaminated-vs-clean comparison is labeled a "reproducibility check" even though
the deltas are real — the `run_health` delta (and warning) is what disambiguates
it. Bump a version field when changing behavior you want A/B-labeled.
