# Design Rationale

This project is a small Claude + Wikipedia QA agent with an evaluation harness.
The goal is not production search. The goal is to make a model use a local
Wikipedia tool responsibly, then measure where that behavior succeeds and fails.

## What I Built

The runnable prototype is a CLI around Claude, a local `search_wikipedia(query:
str)` tool, and the English Wikipedia MediaWiki API. Each answer run records a
trace with the question, model, prompt/tool versions, tool calls, retrieved
evidence, final answer, token usage, latency, and errors. Evals run against
saved traces, so I can re-grade or compare runs without re-calling Claude.

Runtime flow:

```text
question -> CLI -> Claude tool runner -> local Wikipedia tool -> Wikipedia API
         -> Claude answer -> saved trace -> deterministic + optional judge evals
```

## Model And Tool Choices

- **Agent model:** `claude-haiku-4-5` by default. I chose a small model because
  this task is meant to test retrieval-grounded behavior, not how much the model
  already knows from memory. A larger model's recall can be a liability for this
  evaluation if it answers without using retrieved evidence.
- **Judge model:** `claude-opus-4-8` by default. The judge is intentionally
  separate from the answering agent and is used only for evaluation, not answer
  generation.
- **Tool runner:** I used `client.beta.messages.tool_runner` with a local
  `@beta_tool` rather than a hosted search/RAG tool. This keeps the model-facing
  tool boundary explicit and lets the evals inspect exactly what evidence was
  available when the answer was written.

Hosted search was not used because the assignment asks for the Wikipedia
retrieval path to be designed here. The local tool returns JSON with page titles,
URLs, page IDs, snippets, and lead extracts. It also uses timeouts, retries,
backoff, a shared session, and an in-memory cache.

## Prompt Design

The v1 system prompt tells Claude when to search, how to form targeted queries,
when to re-search, how to handle comparisons and ambiguity, and when to refuse
because Wikipedia evidence is insufficient. It also requires a structured ending:

```text
Sources used:
- <Wikipedia page title>
Search used: yes/no
```

The most important prompt choice is the grounding rule: factual claims should be
based on retrieved evidence, not memory. I then evaluated whether that actually
happened instead of assuming the instruction worked.

**v2** keeps everything in v1 and adds two targeted rules, each aimed at a failure
mode the v1 evals exposed: (1) if the top results are list/index/disambiguation
pages, re-search with the exact article name; (2) answer tersely from the
retrieved extracts only, without padding in dates/numbers/names that aren't in the
evidence. v2 is the current default; the v1→v2 A/B below is how I checked whether
those rules actually moved the metrics they targeted.

## Eval Design

The eval suite has 50 cases across 10 behavior categories: factual lookup,
multi-hop lookup, comparison, ambiguous entity, insufficient evidence,
no-search, typo/paraphrase, user pressure to guess, source boundary, and
retrieval noise/distractors. The cases are intended to test behavior, not just
trivia accuracy.

Deterministic checks cover:

- `search_decision_correct`: whether the model searched when expected.
- `expected_page_hit`: whether the needed Wikipedia evidence was retrieved. For
  comparisons and selected multi-hop cases, `required_page_groups` requires both
  sides/steps to be retrieved; for ambiguity cases, `min_distinct_pages` requires
  multiple senses.
- `required_terms_present` and `forbidden_terms_absent`: lightweight answer
  content checks.
- `answer_format_valid`: whether the answer follows the required search/source
  format.
- `declined_when_unanswerable`: a deterministic proxy for not fabricating in
  refusal cases.
- `cited_sources_retrieved`: whether every cited source title was actually
  retrieved by the tool.

These deterministic checks are intentionally cheap and imperfect. They catch
obvious behavior failures, while Claude-as-judge scores the harder questions:
correctness, groundedness against the retrieved evidence, query quality,
ambiguity handling, insufficient-evidence handling, usefulness, and unsupported
claim count. I report Wilson confidence intervals for binary metrics and keep a
run-health check so rate limiting or tool failures do not get mistaken for agent
behavior.

## Results And What I Learned

On the current v2 prompt run (`20260618T004724Z`, n=50; this is the sample run),
graded under the tightened rubric (require-all page groups, min-distinct senses,
cited-source grounding, and `insufficient_evidence` graded `should_search: true`),
the deterministic pass rate was **36/50 = 72%** with a Wilson 95% CI of **58-82%**,
and run health was clean with **0 tool errors**.

Selected deterministic results:

- `search_decision_correct`: 85.1% (40/47 applicable)
- `expected_page_hit`: 69.0%
- `cited_sources_retrieved`: 34/34 (no fabricated citations)
- `answer_format_valid`: 96%
- `declined_when_unanswerable`: 10/10
- `required_terms_present`: 100%

The judge pass across all 50 cases:

- answer correctness mean: 1.92 / 2
- groundedness mean: 1.74 / 2
- grounded-correct: 39/50 = 78%
- unsupported-claim rate: 34/228 = 14.9%

**v1 → v2 A/B (paired, same 50 cases).** v2's two new rules targeted grounding,
and that is where it moved: grounded-correct **70% → 78%**, unsupported-claim rate
**20.4% → 14.9%**, judge groundedness **1.68 → 1.74**, ambiguity_handling
**1.0 → 1.33**. The cost was a small, **statistically non-significant** dip in
deterministic pass (**78% → 72%**; 1 case improved, 4 regressed, McNemar p=0.375,
overlapping CIs), concentrated in `expected_page_hit` (76% → 69%) and
`search_decision_correct` (89% → 85%) — the terseness rule appears to make the
agent search slightly less aggressively. The list-page re-search rule did **not**
lift `expected_page_hit`. So v2 is an honest trade: a real reduction in
unsupported claims (the project's headline gap) against a within-noise retrieval
regression — not a uniform win. The bigger lesson from the eval work is that it
exposes exactly this distinction between *correct* answers and *grounded* ones.

## Failure Modes

- **Correct but ungrounded:** The agent sometimes adds true-looking details from
  memory that were not present in the retrieved extracts.
- **Weak ambiguity handling:** Some ambiguous questions retrieve relevant pages
  but still answer one sense too strongly instead of clearly surfacing multiple
  meanings.
- **Retrieval misses:** Some direct factual queries retrieve list/meta pages
  instead of the target article, so the answer can be correct while the evidence
  check fails.
- **Refuses without confirming:** On 4 of 7 insufficient-evidence cases the agent
  refuses straight from memory without searching to confirm the detail is truly
  absent. The refusal is honest, but it skips the confirming retrieval — a gap
  exposed only after grading these cases `should_search: true`.
- **Pressure-to-guess cases:** The agent usually refuses correctly, but this is
  still the weakest behavioral category in the deterministic summary.

## Iterations

The project was built incrementally:

1. Basic Claude call and CLI.
2. Local Wikipedia retrieval tool.
3. Tool-runner integration and trace capture.
4. Fifty-case eval suite and deterministic grader.
5. Run summaries, Wilson intervals, run health, and failure reports.
6. Claude-as-judge for correctness vs groundedness.
7. Rate-limit handling after an early contaminated run.
8. v1 system prompt and stricter grading for comparisons, ambiguity, refusals,
   and cited sources.
9. v2 prompt (list-page re-search + terse extract-only answering), measured
   against v1 with the paired A/B above.

The most useful iteration was adding grounding-focused evaluation. It changed
the question from "does the answer look right?" to "was the answer supported by
the evidence the system actually retrieved?" — and it let the v1 → v2 change be
judged on the axis it targeted (grounding) rather than on the headline pass rate
alone.

## What I Would Do Next

- Recover v2's small retrieval regression without giving back its grounding gain
  (the terseness rule made the agent search slightly less aggressively).
- Add span-level or extract-level grounding checks instead of only source-title
  checks.
- Improve retrieval for list/meta-page misses — the v2 re-search rule did not move
  `expected_page_hit`, so this needs a different approach (e.g. a tool-side filter
  that demotes list/disambiguation pages).
- Validate the judge with a small human-labeled sample and test-retest runs.
- Add more cases, especially for ambiguity and adversarial pressure.

## Approximate Time Spent

Approximately 4 hours, including implementation, eval design, debugging
retrieval/rate-limit issues, prompt iteration, and writing the rationale.
