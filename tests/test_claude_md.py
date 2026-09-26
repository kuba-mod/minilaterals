"""CLAUDE.md is loaded into every Claude Code session, so it stays short.

Design rationale belongs in ARCHITECTURE.md, directory-specific guidance in the
nested CLAUDE.md files, and anything checkable in a test.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_LINES = 120


def test_root_claude_md_stays_short():
    lines = (ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= MAX_LINES, (
        f"CLAUDE.md is {len(lines)} lines (limit {MAX_LINES}); move detail to "
        "ARCHITECTURE.md, a nested CLAUDE.md, or code comments"
    )
