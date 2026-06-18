"""Claude answer path.

Answers a question via the SDK tool runner with the local search_wikipedia tool
(no hosted web_search). Tool I/O is captured at the tool boundary (an
instrumented tool writes to a per-run recorder) so traces faithfully reflect
what Claude sent and received, without altering the runner's message loop.
"""

import time

import anthropic

from . import config, prompts, wikipedia_tool
from .wikipedia_tool import make_search_wikipedia_tool


def answer_question(question: str) -> dict:
    """Answer a question and return a full trace record.

    Keys:
        question, model, prompt_version, tool_schema_version,
        answer, search_used,
        tool_calls   - [{name, input, output, latency_s}] in execution order,
        raw_messages - the runner's assistant messages as dicts (if available),
        usage        - {input_tokens, output_tokens} summed over the run,
        latency_s    - wall-clock seconds for the runner loop,
        error        - None, or an error string if the API call failed.
    """
    client = anthropic.Anthropic(api_key=config.get_api_key())

    tool_calls = []  # recorder, filled at the tool boundary
    raw_messages = []
    input_tokens = 0
    output_tokens = 0
    final_message = None
    error = None

    tool = make_search_wikipedia_tool(tool_calls)

    # Temperature is only sent when explicitly configured: the default model
    # (claude-opus-4-8 / 4.7 / Fable) rejects `temperature` with a 400.
    extra = {}
    if config.TEMPERATURE is not None:
        extra["temperature"] = config.TEMPERATURE

    start = time.monotonic()
    try:
        runner = client.beta.messages.tool_runner(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=prompts.SYSTEM_PROMPT,
            tools=[tool],
            messages=[{"role": "user", "content": question}],
            **extra,
        )
        for message in runner:
            final_message = message
            raw_messages.append(message.to_dict())
            usage = getattr(message, "usage", None)
            if usage:
                input_tokens += getattr(usage, "input_tokens", 0) or 0
                output_tokens += getattr(usage, "output_tokens", 0) or 0
    except anthropic.APIError as e:
        error = f"{type(e).__name__}: {e}"
    latency_s = time.monotonic() - start

    answer = (
        "".join(b.text for b in final_message.content if b.type == "text")
        if final_message
        else ""
    )

    return {
        "question": question,
        "model": config.MODEL,
        "prompt_version": prompts.PROMPT_VERSION,
        "tool_schema_version": wikipedia_tool.TOOL_SCHEMA_VERSION,
        "answer": answer,
        "search_used": bool(tool_calls),
        "tool_calls": tool_calls,
        "raw_messages": raw_messages,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "latency_s": latency_s,
        "error": error,
    }
