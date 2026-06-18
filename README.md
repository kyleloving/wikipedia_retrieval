# Claude + Wikipedia QA Agent

A small, runnable question-answering system that uses **Claude** with a **local
Wikipedia search tool**, plus a **behavioral eval suite** that grades saved runs
deterministically and (optionally) with Claude-as-judge.

The goal is not a production search engine. It is to make Claude behave like a
trustworthy, evidence-seeking assistant — searching Wikipedia when facts are
needed, grounding answers in what it retrieved, handling ambiguity and missing
evidence honestly — and to **measure** that behavior. See
[docs/design.md](docs/design.md) for the full design and
[docs/rationale.md](docs/rationale.md) for design rationale and results.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate

pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # + pytest, to run the tests

cp .env.example .env                      # then add your ANTHROPIC_API_KEY
```

Requires Python 3.10+.

## Environment variables

Set in `.env` (loaded automatically) or your shell. Only the API key is required.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | Your Anthropic API key. |
| `ANTHROPIC_MODEL` | no | `claude-haiku-4-5` | Answering model. Small by design (see rationale). |
| `ANTHROPIC_MAX_TOKENS` | no | `1024` | Max output tokens for the agent. |
| `ANTHROPIC_TEMPERATURE` | no | unset | Only sent when set. **Note:** Opus 4.x / Fable reject `temperature`; use it only with a model that accepts it (e.g. Haiku/Sonnet). |
| `ANTHROPIC_JUDGE_MODEL` | no | `claude-opus-4-8` | Stronger, independent model for `--judge` / `--summary`. |

## Run one question

```bash
python -m src.app "Who wrote The Structure of Scientific Revolutions?"
python -m src.app -v "Which was founded earlier, Nintendo or Sony?"   # -v shows the search queries
```
The answer renders as markdown in a panel, with a footer: model, whether search
was used, tool-call count, latency, and tokens. Each run also writes a trace
under `artifacts/runs/<run_id>/`.

## Run the demo

```bash
python -m src.app --demo
```
Runs a fixed set of questions spanning factual lookup, comparison, an
unanswerable question, and a creative (no-search) task.

## Run the evals

```bash
# 1. Run cases through the agent, saving one trace per case
python evals/run_evals.py                      # all 50 cases
python evals/run_evals.py --limit 5            # quick subset
python evals/run_evals.py --category comparison

# 2. Grade a run (deterministic; defaults to the latest run)
python evals/grade_run.py
python evals/grade_run.py --summary            # + LLM narrative executive summary (API call)

# 3. Grade or judge a single trace
python evals/grade_trace.py artifacts/runs/<run_id>/traces/trace_000.json
python evals/grade_trace.py <trace.json> --judge        # + Claude-as-judge scoring

# 4. Compare two runs (A/B a prompt/tool change, or check reproducibility)
python evals/compare.py <run1> <run2>
```

A worked example run is committed under [artifacts/sample_run/](artifacts/sample_run/).

## Artifact structure

```text
artifacts/runs/<run_id>/
  manifest.json        # run metadata (model, versions, cases)
  traces/trace_NNN.json   # per case: question, answer, tool calls + outputs, usage, latency
  grades/grade_NNN.json   # per case: deterministic checks (+ judge, if run)
  summary.json         # aggregate metrics, CIs, run health, judge aggregate, executive summary
  summary.csv          # flat metric table (rate + Wilson CI) for spreadsheets/cross-run
  failures.md          # failing cases grouped by category
artifacts/comparisons/<a>_vs_<b>.md   # output of compare.py
artifacts/sample_run/                 # a committed example run
```
Traces and grades are kept separate so traces can be re-graded without re-running
Claude.

## Metrics

**Deterministic (always, free)** — per case and aggregated with a Wilson 95% CI:
- `search_decision_correct` — searched iff it should have
- `expected_page_hit` — an expected Wikipedia page was retrieved
- `required_terms_present` / `forbidden_terms_absent` — answer content checks
- `answer_format_valid` — non-empty answer
- `deterministic_pass` — all applicable checks passed

**Run health** — tool-call failure rate (e.g. rate limiting); flags a run
`unreliable` when retrieval-dependent metrics can't be trusted.

**Judge (opt-in, `--judge` / `--summary`)** — Claude-as-judge scores
`answer_correctness`, **`groundedness`** (supported by retrieved evidence, *not*
memory), `query_quality`, `ambiguity_handling`, `insufficient_evidence_handling`,
`answer_usefulness`, plus `unsupported_claim_rate` and a grounded-correct
composite. Correctness and groundedness are scored independently.

**Executive summary** — every `summary.json` includes a deterministic synthesis
(headline, reliability verdict, key findings, caveats); `--summary` adds an LLM
narrative on top.

## Limitations

- **Small suite (50 cases, 3–8 per category).** Confidence intervals are wide;
  treat category and cross-run differences as directional, not significant.
- **Deterministic checks are shallow.** Term matching is word-boundary
  case-sensitive and page matching is title- or URL-slug based (redirect-safe),
  but they still can't judge truth or grounding — that needs the judge.
- **The judge is an LLM, not ground truth.** Run-level judging retries transient
  failures, but the scores aren't validated against human labels (no test–retest
  or human-agreement study yet).
- **Wikipedia rate limits** can slow eval runs; the tool throttles and retries
  with backoff, and `run_health` flags runs degraded by 429s.
- **Residual grounding gap.** Even with the v1 prompt, ~20% of factual claims
  (n=50 judge) aren't supported by retrieved evidence — the deliberately small
  model still leans on memory at times. Further prompt iteration is future work.

## Project layout

- `src/` — CLI (`app.py`), agent loop (`agent.py`), Wikipedia tool
  (`wikipedia_tool.py`), trace store, config
- `evals/` — cases (`cases.yaml`), runner, graders, stats, comparison
- `tests/` — unit tests (`pytest`)
- `docs/` — design, rationale, steering log
- `artifacts/` — run outputs and the committed sample run
