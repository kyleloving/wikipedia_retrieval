"""User-facing CLI for the Claude + Wikipedia QA agent.

    python -m src.app "Who wrote The Structure of Scientific Revolutions?"
    python -m src.app --demo
    python -m src.app --verbose "..."   # also show the search queries

Each run writes trace JSON under artifacts/runs/<run_id>/.
"""

import argparse
import os
import sys

import anthropic
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel

from . import config, prompts, trace_store, wikipedia_tool
from .agent import answer_question

DEMO_QUESTIONS = [
    "Who wrote The Structure of Scientific Revolutions?",
    "Which was founded earlier, Nintendo or Sony?",
    "What was Ada Lovelace's favorite breakfast?",
    "Brainstorm three names for a Wikipedia QA tool.",
]

console = Console()

# Use a middle dot where the terminal can encode it, a pipe otherwise, so the
# footer never mojibakes on legacy (non-UTF-8) Windows consoles.
_SEP = " · " if "utf" in (console.encoding or "").lower() else " | "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.app",
        description="Ask a question, answered by Claude grounded in Wikipedia.",
    )
    parser.add_argument("question", nargs="?", help="The question to answer.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run a fixed set of demo questions instead of a single question.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also print the Wikipedia search queries Claude issued.",
    )
    return parser


def _render(record: dict, verbose: bool) -> None:
    console.print(f"[bold cyan]Q[/]  {escape(record['question'])}")

    if verbose and record["tool_calls"]:
        console.print("[dim]searches:[/]")
        for i, call in enumerate(record["tool_calls"], 1):
            query = call["input"].get("query")
            console.print(f"  [dim]{i}.[/] {escape(repr(query))}")

    if record["error"]:
        console.print(f"[red]Error:[/] {escape(record['error'])}")

    answer = record["answer"] or "*(no answer)*"
    console.print(
        Panel(Markdown(answer), title="Answer", border_style="cyan", padding=(0, 1))
    )

    search = "[green]yes[/]" if record["search_used"] else "[yellow]no[/]"
    n = len(record["tool_calls"])
    tokens = record["usage"]["input_tokens"] + record["usage"]["output_tokens"]
    sep = f"[dim]{_SEP}[/]"
    fields = [
        f"[dim]model[/] {record['model']}",
        f"search: {search}",
        f"{n} tool call{'' if n == 1 else 's'}",
        f"{record['latency_s']:.1f}s",
        f"{tokens:,} tok",
    ]
    console.print(sep.join(fields))


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.demo:
        questions = DEMO_QUESTIONS
    elif args.question:
        questions = [args.question]
    else:
        parser.print_help()
        return 0

    # Fail fast on a missing key, before creating an empty run directory.
    try:
        config.get_api_key()
    except RuntimeError as e:
        console.print(f"[red]Error:[/] {escape(str(e))}")
        return 1

    run_id = trace_store.start_run()
    trace_files = []
    exit_code = 0

    for i, question in enumerate(questions):
        if i:
            console.rule(style="dim")
        try:
            record = answer_question(question)
        except Exception as e:  # keep the CLI from dumping a traceback
            console.print(f"[red]Error answering this question:[/] {escape(str(e))}")
            record = {
                "question": question,
                "model": config.MODEL,
                "answer": "",
                "search_used": False,
                "tool_calls": [],
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "latency_s": 0.0,
                "error": f"{type(e).__name__}: {e}",
            }
        _render(record, args.verbose)
        path = trace_store.save_trace(run_id, record)
        trace_files.append(os.path.basename(path))
        if record["error"]:
            exit_code = 1

    trace_store.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "model": config.MODEL,
            "prompt_version": prompts.PROMPT_VERSION,
            "tool_schema_version": wikipedia_tool.TOOL_SCHEMA_VERSION,
            "num_questions": len(questions),
            "traces": trace_files,
        },
    )

    console.rule(style="dim")
    console.print(f"[dim]traces saved to[/] {trace_store.run_dir(run_id)}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
