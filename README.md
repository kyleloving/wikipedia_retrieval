# Claude + Wikipedia QA Agent

A small, runnable QA system that uses Claude and Wikipedia to answer questions,
plus a behavioral eval suite. See [docs/design.md](docs/design.md) for the full
design.

> Status: **skeleton only**. The structure is in place; runtime behavior,
> Wikipedia retrieval, and evals are not implemented yet.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then add your ANTHROPIC_API_KEY
```

## Usage

```bash
python -m src.app "Your question here"
python -m src.app --demo            # run a set of demo questions
python -m src.app -v "Your question"  # also show the Wikipedia search queries
```

The CLI answers via Claude using the local `search_wikipedia` tool, renders the
answer as markdown, and shows an info footer (model, whether search was used,
tool-call count, latency, tokens). Trace persistence and evals are not
implemented yet.

## Layout

- `src/` — CLI, agent loop, Wikipedia tool, prompts, traces, config
- `evals/` — eval cases, runners, graders, stats
- `tests/` — unit tests
- `docs/` — design, rationale, steering log, AI transcripts
- `artifacts/` — eval run outputs
