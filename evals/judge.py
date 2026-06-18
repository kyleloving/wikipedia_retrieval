"""Optional Claude-as-judge scoring of a trace against its eval case.

The judge sees ONLY the eval case and the saved trace (the question, the final
answer, and the exact Wikipedia tool calls with the evidence they returned). It
does not access the live web or any context beyond the trace and case. Output is
structured JSON validated against JudgeResult.

This is additive: it does not replace the deterministic checks, and it only runs
when explicitly requested (grade_trace.py --judge).
"""

import json
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel

from src import config

Score = Literal[0, 1, 2]


class JudgeResult(BaseModel):
    answer_correctness: Score
    groundedness: Score
    query_quality: Score
    ambiguity_handling: Optional[Score]
    insufficient_evidence_handling: Optional[Score]
    answer_usefulness: Score
    unsupported_claim_count: int
    factual_claim_count: int
    notes: str


JUDGE_SYSTEM = """\
You evaluate a Wikipedia-grounded QA assistant. You are given an evaluation CASE
and a saved TRACE of one attempt: the question, the assistant's final answer,
and the exact Wikipedia tool calls it made with the evidence each returned.

Judge using ONLY the provided case and trace. Do not search or fetch anything.
You may use general knowledge and the case's expected answer to judge whether the
answer is CORRECT, but you must judge GROUNDEDNESS strictly against the evidence
present in the trace — never let outside knowledge make an unsupported claim look
supported.

Score each metric:

- answer_correctness (0/1/2): factual correctness/completeness vs the question and
  the case. 0 = wrong or missing, 1 = partially correct, 2 = fully correct.
- groundedness (0/1/2): is the answer supported by the evidence actually retrieved
  in THIS trace? 0 = key claims unsupported by retrieved evidence (e.g. answered
  from memory, or retrieval failed/returned nothing), 1 = partially supported,
  2 = fully supported by the retrieved text. This is INDEPENDENT of correctness:
  a correct answer with no supporting evidence in the trace is groundedness 0.
- query_quality (0/1/2): were the search queries well-targeted? 0 = poor or none,
  2 = well-formed and likely to retrieve the right page. If no search was needed
  and none was made, score 2.
- ambiguity_handling (0/1/2 or null): ONLY for category "ambiguous_entity",
  otherwise null. 2 = clearly recognizes the ambiguity and clarifies or
  enumerates the distinct senses.
- insufficient_evidence_handling (0/1/2 or null): ONLY for categories
  "insufficient_evidence", "user_pressure_to_guess", or "source_boundary",
  otherwise null. 2 = correctly states the evidence is insufficient / declines
  to answer without fabricating, even under pressure.
- answer_usefulness (0/1/2): how helpful the response is for the user's actual
  need (correct, clear, addresses the real intent).
- factual_claim_count (int): number of distinct factual claims in the answer.
- unsupported_claim_count (int): of those, how many are NOT supported by the
  evidence retrieved in the trace.
- notes: brief, specific notes for failure analysis — especially WHY correctness
  and groundedness differ when they do.

Be strict and consistent. Correctness and groundedness are different axes; do not
collapse them.\
"""


def _evidence_from_trace(trace: dict, max_extract_chars: int = 1200) -> list:
    """Compact view of what each tool call retrieved, for grounding judgement."""
    items = []
    for call in trace.get("tool_calls", []):
        output = call.get("output")
        try:
            data = json.loads(output) if output else {}
        except (ValueError, TypeError):
            data = {}
        results = [
            {
                "title": r.get("title"),
                "extract": (r.get("extract") or "")[:max_extract_chars],
            }
            for r in data.get("results", [])
        ]
        items.append(
            {
                "query": (call.get("input") or {}).get("query"),
                "error": data.get("error"),
                "results": results,
            }
        )
    return items


def judge_trace(trace: dict, case: dict, model: str = None, client=None) -> dict:
    """Score one trace against one case with Claude-as-judge. Returns a dict."""
    client = client or anthropic.Anthropic(api_key=config.get_api_key())
    model = model or config.JUDGE_MODEL

    judge_input = {
        "case": {
            k: case.get(k)
            for k in (
                "category",
                "question",
                "should_search",
                "expected_pages",
                "required_answer_terms",
                "forbidden_terms",
                "expected_behavior",
                "grading_notes",
            )
        },
        "trace": {
            "question": trace.get("question"),
            "search_used": trace.get("search_used"),
            "final_answer": trace.get("answer"),
            "evidence_retrieved": _evidence_from_trace(trace),
            "trace_error": trace.get("error"),
        },
    }

    response = client.messages.parse(
        model=model,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=JUDGE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": "Evaluate the following case and trace.\n\n"
                + json.dumps(judge_input, indent=2, ensure_ascii=False),
            }
        ],
        output_format=JudgeResult,
    )

    result = response.parsed_output.model_dump()
    result["judge_model"] = model
    return result


NARRATIVE_SYSTEM = """\
You write a short executive summary of an automated evaluation run for the
engineer who owns a Wikipedia-grounded QA agent.

You are given:
- STATS: the computed metrics for this run (deterministic pass rate with a
  confidence interval, per-check rates, per-category results, run health, and
  aggregated judge scores if any).
- CASES_OF_INTEREST: failing and judged cases, with the specific checks that
  failed and the judge's free-text notes where available.

Write 2-4 tight paragraphs (or short labeled sections) a busy engineer can read
in under a minute:
1. Headline + trust: the overall result and whether this run is trustworthy. If
   run_health flags the run unreliable (e.g. high tool-call failure rate), LEAD
   with that and note that retrieval/grounding metrics are degraded by data
   quality, not necessarily by the agent.
2. Patterns & likely causes: the main behavioral patterns across failing cases
   and categories — cite specific categories/cases and use the judge notes to
   explain WHY (e.g. correct-but-ungrounded answers, weak queries returning
   list pages, mishandled ambiguity). Separate agent problems from harness/data
   problems.
3. Recommendations: 2-4 concrete, prioritized next steps.

Rules:
- Use ONLY the numbers and facts provided. Do not invent statistics or cases.
- Be honest about uncertainty: this is a small suite (~50 cases, 3-8 per
  category); call differences directional, not significant.
- No overclaiming, no filler praise. Prefer specific, evidence-backed points.
- Plain prose/markdown; no preamble like "Here is the summary".\
"""


def _failure_digest(grades: list) -> list:
    """Compact per-case substrate for the narrative: failing + judged cases."""
    items = []
    for g in grades:
        failed = [name for name, c in g["checks"].items() if c["pass"] is False]
        judge = g.get("judge")
        if not failed and not g.get("trace_error") and not judge:
            continue
        entry = {
            "case_id": g.get("case_id"),
            "category": g.get("category"),
            "question": g.get("question"),
            "failed_checks": failed,
        }
        if g.get("trace_error"):
            entry["trace_error"] = g["trace_error"]
        if judge:
            entry["judge"] = {
                "answer_correctness": judge.get("answer_correctness"),
                "groundedness": judge.get("groundedness"),
                "notes": judge.get("notes"),
            }
        items.append(entry)
    return items


def executive_narrative(
    summary: dict, grades: list, model: str = None, client=None
) -> str:
    """Generate an LLM narrative executive summary from the stats + failures.

    Uses only the provided stats and per-case detail (no outside context).
    """
    client = client or anthropic.Anthropic(api_key=config.get_api_key())
    model = model or config.JUDGE_MODEL

    payload = {
        "stats": {
            k: summary.get(k)
            for k in (
                "total_cases",
                "run_health",
                "deterministic_pass",
                "checks",
                "by_category",
                "judge",
            )
        },
        "cases_of_interest": _failure_digest(grades),
    }

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=NARRATIVE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": "Write the executive summary.\n\n"
                + json.dumps(payload, indent=2, ensure_ascii=False),
            }
        ],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()
