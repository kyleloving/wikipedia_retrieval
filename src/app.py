"""User-facing CLI for the Claude + Wikipedia QA agent.

    python -m src.app "Who wrote The Structure of Scientific Revolutions?"
    python -m src.app --demo
    python -m src.app --verbose "..."   # also show the search queries
"""

import argparse
import sys

import anthropic
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel

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
            console.print(f"  [dim]{i}.[/] {escape(repr(call['query']))}")

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

    try:
        for i, question in enumerate(questions):
            if i:
                console.rule(style="dim")
            _render(answer_question(question), args.verbose)
    except RuntimeError as e:  # missing API key
        console.print(f"[red]Error:[/] {escape(str(e))}")
        return 1
    except anthropic.APIError as e:  # network / API failure
        console.print(f"[red]Claude API error:[/] {escape(str(e))}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
