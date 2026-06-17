"""Claude answer path.

Answers a question via the SDK tool runner with the local search_wikipedia tool
(no hosted web_search). Returns the answer plus lightweight run metadata
(tool-call queries, token usage, latency) useful for inspection and debugging.
Full trace capture and persistence come in a later step.
"""

import time

import anthropic

from . import config
from .wikipedia_tool import search_wikipedia


def answer_question(question: str) -> dict:
    """Answer a question via Claude + the local search_wikipedia tool.

    Returns a dict:
        question     - the input question
        answer       - Claude's final text answer
        search_used  - True iff Claude invoked the tool at least once
        model        - the model used
        tool_calls   - [{"name", "query"}] in call order
        usage        - {"input_tokens", "output_tokens"} summed over the run
        latency_s    - wall-clock seconds for the runner loop
    """
    client = anthropic.Anthropic(api_key=config.get_api_key())

    runner = client.beta.messages.tool_runner(
        model=config.MODEL,
        max_tokens=config.MAX_TOKENS,
        tools=[search_wikipedia],
        messages=[{"role": "user", "content": question}],
    )

    tool_calls = []
    input_tokens = 0
    output_tokens = 0
    final_message = None

    start = time.monotonic()
    for message in runner:
        final_message = message
        usage = getattr(message, "usage", None)
        if usage:
            input_tokens += getattr(usage, "input_tokens", 0) or 0
            output_tokens += getattr(usage, "output_tokens", 0) or 0
        for block in message.content:
            if block.type == "tool_use":
                tool_calls.append(
                    {"name": block.name, "query": dict(block.input).get("query")}
                )
    latency_s = time.monotonic() - start

    answer = (
        "".join(b.text for b in final_message.content if b.type == "text")
        if final_message
        else ""
    )

    return {
        "question": question,
        "answer": answer,
        "search_used": bool(tool_calls),
        "model": config.MODEL,
        "tool_calls": tool_calls,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "latency_s": latency_s,
    }
