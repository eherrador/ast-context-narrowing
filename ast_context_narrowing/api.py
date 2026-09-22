"""High-level convenience API: turn a unified diff + before/after source into
AST-narrowed context, without the caller touching hunks/byte-ranges/Tree-sitter
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .diff import line_range_to_byte_range, parse_diff_hunks
from .languages import DEFINITION_NODE_TYPES, get_parser
from .narrowing import extract_structural_regions


@dataclass(frozen=True)
class NarrowingResult:
    """Result of narrowing a diff to its structural context.

    `context`/`output` are None when narrowing found no definition node to anchor to
    (e.g. the language isn't registered, the diff only touches top-level statements
    outside any definition, or the source failed to parse) -- the caller decides the
    fallback (typically: use the whole file instead).
    """
    context: str | None
    output: str | None
    scope: Literal["structural", "unavailable"]


def narrow_diff_context(
    language: str,
    before_source: bytes,
    after_source: bytes,
    unified_diff: str,
) -> NarrowingResult:
    """
    Given a unified diff patch and the before/after source of one file, return the
    AST-narrowed context (before) and output (after) text: the smallest enclosing
    structural definition(s) touched by the diff, instead of the whole file.

    `language` must be one of `ast_context_narrowing.languages.SUPPORTED_LANGUAGES`.
    Raises ImportError if tree-sitter + tree-sitter-language-pack aren't installed.
    """
    definition_types = DEFINITION_NODE_TYPES.get(language)
    if not definition_types:
        return NarrowingResult(context=None, output=None, scope="unavailable")

    hunks = parse_diff_hunks(unified_diff)
    if not hunks:
        return NarrowingResult(context=None, output=None, scope="unavailable")

    parser = get_parser(language)
    before_tree = parser.parse(before_source)
    after_tree = parser.parse(after_source)

    if before_tree.root_node.has_error or after_tree.root_node.has_error:
        return NarrowingResult(context=None, output=None, scope="unavailable")

    before_ranges = [
        line_range_to_byte_range(before_source, h["old_start"], h["old_len"]) for h in hunks
    ]
    after_ranges = [
        line_range_to_byte_range(after_source, h["new_start"], h["new_len"]) for h in hunks
    ]

    context = extract_structural_regions(before_source, before_tree.root_node, before_ranges, definition_types)
    output = extract_structural_regions(after_source, after_tree.root_node, after_ranges, definition_types)

    if context is None or output is None:
        return NarrowingResult(context=None, output=None, scope="unavailable")

    return NarrowingResult(context=context, output=output, scope="structural")
