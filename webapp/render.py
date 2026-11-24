"""Markdown to safe HTML. Raw HTML in the source is escaped, never rendered:
Claude's output can quote job postings, which are untrusted."""

from __future__ import annotations

from markdown_it import MarkdownIt
from markupsafe import Markup

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True}).enable(["table", "strikethrough"])


def markdown(text: str | None) -> Markup:
    return Markup(_md.render(text or ""))
