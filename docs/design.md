# Design: Claude + Wikipedia QA Agent

## Goal

Build a small, runnable QA system that uses Claude and Wikipedia to answer user questions.

The system should demonstrate:

* effective prompt design;
* local client-side tool use;
* Wikipedia-grounded answering;
* trace capture;
* behavioral evals;
* failure analysis;
* statistically honest reporting.

The goal is not to build a production search engine. The goal is to make Claude behave like a trustworthy evidence-seeking assistant and evaluate that behavior.

---

## Assignment Constraints

The system must:

* use an Anthropic model through the Anthropic API;
* use Wikipedia as the retrieval source;
* expose a model-facing tool named `search_wikipedia(query: str)`;
* include a runnable prototype;
* include an eval suite;
* include clear setup instructions;
* include design rationale and AI development transcripts.

The system must not use:

* Anthropic hosted `web_search`;
* hosted RAG;
* browser tools;
* Perplexity-style search;
* LangChain agents;
* vector databases;
* local Wikipedia dumps;
* MCP as the default runtime;
* Managed Agents / Sessions / Environments as the default runtime.

---

## Core Architecture

```text
user question
  → CLI
  → Claude via Anthropic SDK Tool Runner
  → local search_wikipedia tool
  → Wikipedia API retrieval
  → Claude final answer
  → trace saved
  → grader evaluates trace
```

Runtime path:

1. The user submits a question through the CLI.
2. The app calls Claude using the official Anthropic Python SDK.
3. The local `search_wikipedia` function is registered with `@beta_tool`.
4. `client.beta.messages.tool_runner` handles the client-tool loop.
5. When Claude calls `search_wikipedia`, the local Python function queries Wikipedia.
6. Claude receives the tool result and produces a final answer.
7. The app records a full trace.
8. A separate grading step evaluates the trace.

The default implementation uses the SDK Tool Runner instead of a manually implemented `tool_use` / `tool_result` loop. This keeps the runtime idiomatic while preserving the important boundary: Wikipedia retrieval is local project code, not a hosted search tool.

Fallback rule:

> If Tool Runner does not expose enough information for trace capture, use a manual Messages API loop. Do not switch to Managed Agents or hosted search.

---

## System Behavior

Claude should:

* search Wikipedia when factual evidence is needed;
* avoid searching for creative or conversational tasks;
* form targeted search queries;
* search again when results are ambiguous or insufficient;
* compare entities using the same attribute;
* handle ambiguous entities explicitly;
* say when Wikipedia does not provide enough evidence;
* avoid unsupported factual claims;
* list Wikipedia page titles used;
* include `Search used: yes/no`.

Example final answer shape:

```text
[Direct answer]

Sources used:
- [Wikipedia page title]
- [Wikipedia page title]

Search used: yes/no
```

---

## Wikipedia Tool

Model-facing tool:

```python
search_wikipedia(query: str) -> str
```

The tool should:

* search English Wikipedia;
* return compact evidence;
* include page titles, URLs, page IDs, snippets, and extracts;
* return the top 3–5 results;
* use timeouts;
* cache repeated queries locally;
* return structured errors;
* avoid answer synthesis.

The tool returns JSON as a string because it is consumed by Claude through the Anthropic SDK tool interface.

---

## Repository Shape

```text
src/
  app.py
  agent.py
  wikipedia_tool.py
  prompts.py
  schemas.py
  trace_store.py
  config.py

evals/
  cases.yaml
  run_evals.py
  grade_trace.py
  grade_run.py
  stats.py
  compare_runs.py

docs/
  design.md
  rationale.md
  steering_log.md
  ai_transcripts/

tests/
  test_wikipedia_tool.py
  test_stats.py
  test_grade_trace.py

artifacts/
  sample_run/
```

---

## Evaluation Design

The eval suite measures behavior, not just trivia accuracy.

Case categories:

* factual lookup;
* multi-hop lookup;
* comparison;
* ambiguous entity;
* insufficient evidence;
* no-search behavior;
* typo or paraphrase robustness;
* user pressure to guess.

Primary deterministic metrics:

* `search_decision_correct`;
* `tool_call_count`;
* `expected_page_hit`;
* `required_terms_present`;
* `forbidden_terms_absent`;
* `source_titles_valid`;
* `answer_format_valid`.

Optional judge metrics:

* `answer_correctness`;
* `groundedness`;
* `query_quality`;
* `ambiguity_handling`;
* `insufficient_evidence_handling`;
* `answer_usefulness`;
* `unsupported_claim_count`;
* `unsupported_claim_rate`.

Overall pass should require both useful output and acceptable behavior. A correct answer that was produced without required evidence should not receive full credit.

---

## Statistical Reporting

For binary metrics, report:

* passed count;
* total count;
* pass rate;
* Wilson 95% confidence interval.

For numeric judge scores, report:

* mean;
* bootstrap 95% confidence interval.

For prompt-version comparisons, prefer paired case-level comparisons:

* improved cases;
* regressed cases;
* unchanged cases;
* McNemar-style counts for binary outcomes;
* paired bootstrap or paired t-test for numeric deltas if useful.

Because the eval suite is small, results should be interpreted directionally rather than as production-grade benchmark claims.

---

## Artifacts

Each eval run writes:

```text
artifacts/runs/<run_id>/
  manifest.json
  traces/
  grades/
  summary.json
  summary.csv
  category_summary.csv
  failures.md
```

Each trace should include:

* question;
* model;
* prompt version;
* tool schema version;
* raw runner messages where available;
* normalized tool calls;
* tool inputs;
* tool outputs;
* final answer;
* whether search was used;
* usage and latency if available;
* errors.

Traces and grades are separate so traces can be re-graded without rerunning Claude.

---

## Failure Taxonomy

Failed cases should be grouped by primary cause:

* `bad_search_decision`;
* `weak_query`;
* `retrieval_failure`;
* `missing_expected_page`;
* `incorrect_synthesis`;
* `unsupported_claim`;
* `poor_ambiguity_handling`;
* `poor_insufficient_evidence_handling`;
* `source_misattribution`;
* `format_failure`;
* `tool_error`;
* `grader_error`.

Prompt changes should target failure categories, not individual examples.

---

## Non-Goals

This project intentionally does not include:

* production search ranking;
* full article indexing;
* vector retrieval;
* local Wikipedia dump processing;
* multi-agent orchestration;
* web browsing;
* UI beyond a CLI;
* complex deployment;
* comprehensive benchmark-scale evals.

---

## Success Criteria

The project is successful if:

* the CLI works end-to-end;
* Claude can call the local `search_wikipedia` tool;
* Wikipedia retrieval works without hosted search;
* final answers include source pages and search-used status;
* traces are saved;
* evals grade saved traces;
* summary metrics include uncertainty;
* failures are categorized honestly;
* prompt iterations are based on eval results;
* a reviewer can run the demo quickly.
