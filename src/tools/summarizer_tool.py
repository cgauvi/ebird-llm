"""
summarizer_tool.py — LangChain tool that saves a large raw output to disk and
returns a compact Markdown summary with a reference to the saved file.
"""

from langchain.tools import tool

from src.utils.summarizer import summarize_text


@tool
def summarize_output(raw_output: str) -> str:
    """Save a large raw text output to a temporary file and return a compact
    Markdown summary.

    Use this tool when a previous tool returned a very large amount of text
    that is too long to include verbatim in the conversation.  The full
    content is written to a local file so the user can inspect it; the
    returned summary includes the file path, character/line counts, and a
    500-character preview.

    Args:
        raw_output: The full raw text output to summarize and save.

    Returns:
        A Markdown-formatted summary containing the saved file path, total
        character and line counts, and a short content preview.
    """
    return summarize_text(raw_output, title="tool_output")


SUMMARIZER_TOOLS = [summarize_output]
