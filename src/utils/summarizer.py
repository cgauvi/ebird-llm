"""
summarizer.py — Utility for handling large tool outputs.

When a tool returns text that exceeds MAX_TOOL_OUTPUT_CHARS, the full content
is written to a uniquely named file under SUMMARIES_DIR and a compact Markdown
summary (with file path, character/line counts, and a 500-char preview) is
returned instead.

Public API
----------
summarize_text(raw, title="output", max_chars=MAX_TOOL_OUTPUT_CHARS) -> str
    Return ``raw`` unchanged when it is short enough; otherwise save and
    return a compact summary.
"""

import datetime
import json
import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Characters at or below this threshold are returned as-is.
MAX_TOOL_OUTPUT_CHARS: int = int(os.environ.get("EBIRD_MAX_TOOL_OUTPUT_CHARS", "4000"))

# Directory where full outputs are persisted.
SUMMARIES_DIR: Path = Path(tempfile.gettempdir()) / "ebird_summaries"


# ---------------------------------------------------------------------------
# Core helper
# ---------------------------------------------------------------------------


def summarize_text(
    raw: str,
    title: str = "output",
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> str:
    """Return *raw* unchanged if it fits within *max_chars*; otherwise save
    the full content to a temp file and return a compact Markdown summary.

    Parameters
    ----------
    raw:
        The raw text to (potentially) summarize.
    title:
        Short label used in the output filename and summary heading.
    max_chars:
        Character threshold.  Content at or below this length is returned
        unchanged.

    Returns
    -------
    str
        The original ``raw`` string, or a Markdown summary that includes the
        path to the saved file, character/line counts, and a 500-char preview.
    """
    if len(raw) <= max_chars:
        return raw

    # ------------------------------------------------------------------
    # Persist full content to a uniquely named file
    # ------------------------------------------------------------------
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_title = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in title
    )[:40]

    # Use .json extension (and pretty-print) when the content is valid JSON.
    try:
        parsed = json.loads(raw)
        ext = ".json"
        file_content = json.dumps(parsed, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        ext = ".txt"
        file_content = raw

    filepath = SUMMARIES_DIR / f"{safe_title}_{timestamp}{ext}"
    filepath.write_text(file_content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Build compact Markdown summary
    # ------------------------------------------------------------------
    line_count = raw.count("\n") + 1
    preview = raw[:500].rstrip() + ("\n…" if len(raw) > 500 else "")

    summary = "\n".join([
        f"## Summary: {title}",
        "",
        f"- **Total characters:** {len(raw):,}",
        f"- **Total lines:** {line_count:,}",
        f"- **Full content saved to:** `{filepath}`",
        "",
        "### Preview (first 500 chars)",
        "```",
        preview,
        "```",
    ])
    return summary
