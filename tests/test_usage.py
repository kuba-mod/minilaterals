"""LLM usage metering (pipeline/usage.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline import usage


@pytest.fixture
def meter():
    return usage.UsageMeter()


def test_tokens_and_calls_accumulate(meter):
    meter.record("m", 1000, 200)
    meter.record("m", 500, 100)
    assert (meter.calls, meter.input_tokens, meter.output_tokens) == (2, 1500, 300)


def test_missing_usage_is_counted_separately_not_as_zero_cost(meter):
    meter.record("m", None, None)
    assert meter.calls == 1
    assert meter.missing == 1
    assert meter.input_tokens == 0


def test_estimated_cost_uses_per_million_rates(meter):
    meter.record("m", 2_000_000, 1_000_000)
    assert meter.estimated_cost(0.14, 0.40) == pytest.approx(0.68)


def test_record_openai_reads_prompt_and_completion_tokens(monkeypatch):
    fresh = usage.UsageMeter()
    monkeypatch.setattr(usage, "METER", fresh)
    monkeypatch.setattr(usage, "_registered", True)  # don't register atexit in tests
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3))
    usage.record_openai("gemma4", response)
    assert (fresh.input_tokens, fresh.output_tokens) == (12, 3)


def test_record_anthropic_reads_input_and_output_tokens(monkeypatch):
    fresh = usage.UsageMeter()
    monkeypatch.setattr(usage, "METER", fresh)
    monkeypatch.setattr(usage, "_registered", True)
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=2))
    usage.record_anthropic("haiku", response)
    assert (fresh.input_tokens, fresh.output_tokens) == (7, 2)


def test_response_without_usage_block_does_not_raise(monkeypatch):
    fresh = usage.UsageMeter()
    monkeypatch.setattr(usage, "METER", fresh)
    monkeypatch.setattr(usage, "_registered", True)
    usage.record_openai("gemma4", SimpleNamespace(usage=None))
    assert fresh.missing == 1


def test_report_appends_step_summary(monkeypatch, tmp_path):
    fresh = usage.UsageMeter()
    fresh.record("gemma4:latest", 1_000_000, 500_000)
    monkeypatch.setattr(usage, "METER", fresh)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_M", "1")
    monkeypatch.setenv("LLM_PRICE_OUTPUT_PER_M", "2")
    usage._report()
    text = summary.read_text()
    assert "LLM usage" in text
    assert "$2.0000" in text  # 1M in × $1 + 0.5M out × $2


def test_blank_price_variable_falls_back_to_default(monkeypatch):
    # The workflow passes '' when the repo variable is unset.
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_M", "")
    assert usage._price("LLM_PRICE_INPUT_PER_M", 0.14) == 0.14
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_M", "not-a-number")
    assert usage._price("LLM_PRICE_INPUT_PER_M", 0.14) == 0.14


def test_report_is_silent_when_no_calls_were_made(monkeypatch, capsys):
    monkeypatch.setattr(usage, "METER", usage.UsageMeter())
    usage._report()
    assert capsys.readouterr().out == ""
