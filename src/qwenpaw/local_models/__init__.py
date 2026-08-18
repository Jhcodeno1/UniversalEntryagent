# -*- coding: utf-8 -*-
"""Minimal local model helpers kept for tool-call tag parsing."""

from .tag_parser import parse_tool_calls_from_text
from .tag_parser import text_contains_tool_call_tag

__all__ = [
    "parse_tool_calls_from_text",
    "text_contains_tool_call_tag",
]
