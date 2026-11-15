"""Volume metering for LLM calls: calls, tokens and characters, per pipeline step.

Providers call `record_openai` / `record_anthropic` after each response. When the
process exits, one summary is printed and — under GitHub Actions — a row is
appended to `$GITHUB_STEP_SUMMARY`, labelled with the command that produced it.

Tokens are whatever the provider's response reports, so they are exact. Characters
are counted locally (system prompt + prompt in, response text out) and are always
available, which makes them the fallback when a response carries no usage block.
No price is applied here: turning volume into cost is done separately.
"""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path


class UsageMeter:
    def __init__(self) -> None:
        self.calls = 0
        self.missing_tokens = 0  # responses that carried no usage block
        self.input_tokens = 0
        self.output_tokens = 0
        self.input_chars = 0
        self.output_chars = 0
        self.models: set[str] = set()

    def record(
        self,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        input_chars: int = 0,
        output_chars: int = 0,
    ) -> None:
        self.calls += 1
        self.models.add(model)
        self.input_chars += input_chars
        self.output_chars += output_chars
        if input_tokens is None and output_tokens is None:
            self.missing_tokens += 1
            return
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)


METER = UsageMeter()
_registered = False


def _label() -> str:
    argv = sys.argv
    if not argv:
        return "llm calls"
    return " ".join([Path(argv[0]).stem, *argv[1:]])


def format_summary(meter: UsageMeter, label: str) -> list[str]:
    lines = [
        f"LLM usage — {label}",
        f"  model(s): {', '.join(sorted(meter.models))}",
        f"  calls: {meter.calls}",
        f"  tokens: {meter.input_tokens:,} in / {meter.output_tokens:,} out",
        f"  characters: {meter.input_chars:,} in / {meter.output_chars:,} out",
    ]
    if meter.missing_tokens:
        lines.append(f"  note: {meter.missing_tokens} call(s) returned no token counts; see characters")
    return lines


def _report() -> None:
    if not METER.calls:
        return
    label = _label()
    print("\n" + "\n".join(format_summary(METER, label)))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    missing = f" ({METER.missing_tokens} without token counts)" if METER.missing_tokens else ""
    row = (
        f"| `{label}` | {', '.join(sorted(METER.models))} | {METER.calls} | "
        f"{METER.input_tokens:,} | {METER.output_tokens:,}{missing} | "
        f"{METER.input_chars:,} | {METER.output_chars:,} |\n"
    )
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            # Several steps append to the same file; a header per step keeps each
            # table self-contained.
            fh.write(
                "\n#### LLM usage\n\n"
                "| step | model | calls | tokens in | tokens out | chars in | chars out |\n"
                "|---|---|---:|---:|---:|---:|---:|\n" + row
            )
    except OSError as exc:  # a summary failure must never fail the job
        print(f"  (could not write step summary: {exc})")


def record_openai(model: str, response, chars_in: int, chars_out: int) -> None:
    """Count a chat-completions response (Ollama's OpenAI-compatible endpoint)."""
    u = getattr(response, "usage", None)
    record(model, getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None), chars_in, chars_out)


def record_anthropic(model: str, response, chars_in: int, chars_out: int) -> None:
    """Count an Anthropic Messages response."""
    u = getattr(response, "usage", None)
    record(model, getattr(u, "input_tokens", None), getattr(u, "output_tokens", None), chars_in, chars_out)


def record(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    input_chars: int = 0,
    output_chars: int = 0,
) -> None:
    """Count one model call; arranges for a summary to print at process exit."""
    global _registered
    METER.record(model, input_tokens, output_tokens, input_chars, output_chars)
    if not _registered:
        atexit.register(_report)
        _registered = True
