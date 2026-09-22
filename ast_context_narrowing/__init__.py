"""ast-context-narrowing: narrow a code diff to the structural definition it touched.

Given a unified diff and a file's before/after source, tools that build training data
or review context from diffs (LLM fine-tuning datasets, code-review bots, changelog
generators, ...) commonly use either the raw diff hunk (too little: no surrounding
signature/class context) or the whole file (too much: drowns a one-line fix in
hundreds of unrelated lines). This library walks the Tree-sitter AST from the changed
byte range up to the smallest enclosing definition -- a method, function, class, or
field -- and returns just that.

Quick start:

    from ast_context_narrowing import narrow_diff_context

    result = narrow_diff_context("typescript", before_bytes, after_bytes, unified_diff)
    if result.scope == "structural":
        print(result.context)  # the enclosing method/class before the change
        print(result.output)   # the same, after

See README.md for the full narrowing rules and why each one exists.
"""

from .api import NarrowingResult, narrow_diff_context
from .languages import DEFINITION_NODE_TYPES, EXTENSION_LANGUAGE_MAP, SUPPORTED_LANGUAGES, language_for_path
from .narrowing import (
    collect_comment_nodes,
    extract_structural_regions,
    find_enclosing_node,
    merge_byte_ranges,
    skip_leading_comments,
)
from .diff import line_range_to_byte_range, parse_diff_hunks

__version__ = "0.1.0"

__all__ = [
    "narrow_diff_context",
    "NarrowingResult",
    "SUPPORTED_LANGUAGES",
    "DEFINITION_NODE_TYPES",
    "EXTENSION_LANGUAGE_MAP",
    "language_for_path",
    "parse_diff_hunks",
    "line_range_to_byte_range",
    "find_enclosing_node",
    "collect_comment_nodes",
    "skip_leading_comments",
    "merge_byte_ranges",
    "extract_structural_regions",
]
