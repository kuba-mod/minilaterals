"""Token-usage metering for LLM calls, so a paid provider's spend is visible per run.

Providers call `record(model, input_tokens, output_tokens)` after each response.
When the process exits, one summary is printed and — under GitHub Actions —
appended to `$GITHUB_STEP_SUMMARY`, labelled with the command that produced it.
Token counts come straight from the provider's response and are exact. The cost
is an *estimate*: tokens × per-million rates you supply, because the API reports
no price. Set the rates from the provider's pricing page:

  LLM_PRICE_INPUT_PER_M    USD per million input tokens   (default 0.14)
  LLM_PRICE_OUTPUT_PER_M   USD per million output tokens  (default 0.40)

The defaults are third-party figures for `gemma4:31b` on Ollama Cloud, not an
official price list; override them via repo variables. Nothing here reads the
account balance — compare the estimate with the balance on ollama.com.
"""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

DEFAULT_PRICE_INPUT_PER_M = 0.14
DEFAULT_PRICE_OUTPUT_PER_M = 0.40


class UsageMeter:
    def __init__(self) -> None:
        self.calls = 0
        self.missing = 0  # responses that carried no usage block
        self.input_tokens = 0
        self.output_tokens = 0
        self.models: set[str] = set()

    def record(self, model: str, input_tokens: int | None, output_tokens: int | None) -> None:
        self.calls += 1
        self.models.add(model)
        if input_tokens is None and output_tokens is None:
            self.missing += 1
            return
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)

    def estimated_cost(self, price_in: float, price_out: float) -> float:
        return self.input_tokens / 1_000_000 * price_in + self.output_tokens / 1_000_000 * price_out


METER = UsageMeter()
_registered = False


def _price(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _label() -> str:
    argv = sys.argv
    if not argv:
        return "llm calls"
    return " ".join([Path(argv[0]).stem, *argv[1:]])


def format_summary(meter: UsageMeter, label: str, price_in: float, price_out: float) -> list[str]:
    cost = meter.estimated_cost(price_in, price_out)
    lines = [
        f"LLM usage — {label}",
        f"  model(s): {', '.join(sorted(meter.models))}",
        f"  calls: {meter.calls}",
        f"  tokens: {meter.input_tokens:,} in / {meter.output_tokens:,} out",
        f"  estimated cost: ${cost:.4f}  (at ${price_in}/M in, ${price_out}/M out)",
    ]
    if meter.missing:
        lines.append(f"  note: {meter.missing} call(s) returned no usage data and are not counted")
    return lines


def _report() -> None:
    if not METER.calls:
        return
    price_in = _price("LLM_PRICE_INPUT_PER_M", DEFAULT_PRICE_INPUT_PER_M)
    price_out = _price("LLM_PRICE_OUTPUT_PER_M", DEFAULT_PRICE_OUTPUT_PER_M)
    lines = format_summary(METER, _label(), price_in, price_out)
    print("\n" + "\n".join(lines))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        cost = METER.estimated_cost(price_in, price_out)
        row = (
            f"| `{_label()}` | {', '.join(sorted(METER.models))} | {METER.calls} | "
            f"{METER.input_tokens:,} | {METER.output_tokens:,} | ${cost:.4f} |\n"
        )
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                # Several steps append to the same file; each writes its own table,
                # so a header per step keeps the rows readable.
                fh.write(
                    "\n#### LLM usage (estimated cost)\n\n"
                    "| step | model | calls | input tokens | output tokens | est. cost |\n"
                    "|---|---|---:|---:|---:|---:|\n" + row
                )
        except OSError as exc:  # a summary failure must never fail the job
            print(f"  (could not write step summary: {exc})")


def record_openai(model: str, response) -> None:
    """Count a chat-completions response (Ollama's OpenAI-compatible endpoint)."""
    usage = getattr(response, "usage", None)
    record(model, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


def record_anthropic(model: str, response) -> None:
    """Count an Anthropic Messages response."""
    usage = getattr(response, "usage", None)
    record(model, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))


def record(model: str, input_tokens: int | None, output_tokens: int | None) -> None:
    """Count one model call; arranges for a summary to print at process exit."""
    global _registered
    METER.record(model, input_tokens, output_tokens)
    if not _registered:
        atexit.register(_report)
        _registered = True
