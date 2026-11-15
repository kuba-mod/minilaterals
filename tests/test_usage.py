"""LLM usage metering (pipeline/usage.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline import usage


@pytest.fixture
def fresh(monkeypatch):
    meter = usage.UsageMeter()
    monkeypatch.setattr(usage, "METER", meter)
    monkeypatch.setattr(usage, "_registered", True)  # don't register atexit in tests
    return meter


def test_tokens_chars_and_calls_accumulate():
    meter = usage.UsageMeter()
    meter.record("m", 1000, 200, 4000, 800)
    meter.record("m", 500, 100, 2000, 400)
    assert (meter.calls, meter.input_tokens, meter.output_tokens) == (2, 1500, 300)
    assert (meter.input_chars, meter.output_chars) == (6000, 1200)


def test_missing_token_counts_still_record_characters():
    meter = usage.UsageMeter()
    meter.record("m", None, None, 900, 100)
    assert meter.calls == 1
    assert meter.missing_tokens == 1
    assert meter.input_tokens == 0
    assert (meter.input_chars, meter.output_chars) == (900, 100)


def test_record_ollama_reads_prompt_and_completion_tokens(fresh):
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3))
    usage.record_ollama("gemma4", response, 50, 9)
    assert (fresh.input_tokens, fresh.output_tokens) == (12, 3)
    assert (fresh.input_chars, fresh.output_chars) == (50, 9)


def test_record_anthropic_reads_input_and_output_tokens(fresh):
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=2))
    usage.record_anthropic("haiku", response, 30, 5)
    assert (fresh.input_tokens, fresh.output_tokens) == (7, 2)


def test_response_without_usage_block_does_not_raise(fresh):
    usage.record_ollama("gemma4", SimpleNamespace(usage=None), 40, 8)
    assert fresh.missing_tokens == 1
    assert fresh.input_chars == 40


def test_report_appends_step_summary(fresh, monkeypatch, tmp_path):
    fresh.record("gemma4:latest", 1_000_000, 500_000, 4_000_000, 2_000_000)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    usage._report()
    text = summary.read_text()
    assert "LLM usage" in text
    assert "1,000,000" in text and "4,000,000" in text
    assert "$" not in text  # volume only; no pricing


def test_report_is_silent_when_no_calls_were_made(fresh, capsys):
    usage._report()
    assert capsys.readouterr().out == ""
