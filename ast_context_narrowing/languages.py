"""Language registration: file-extension mapping and per-language "definition" node types.

A "definition" node is the smallest unit of a source file that's worth treating as a
standalone unit of context — a method, a function, a class, a field — as opposed to an
arbitrary statement or expression. `narrow()` walks a Tree-sitter AST upward from a
changed byte range until it finds a node whose type is in this set.
"""

from __future__ import annotations

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".cs": "csharp",
}

# Per-language sets of Tree-sitter node types treated as definitions. Keyed by the
# same language strings tree-sitter-language-pack uses (via `get_parser`/`get_language`).
#
# Hybrid-grammar languages (Razor/.cshtml, Vue SFCs, etc.) are deliberately absent —
# narrowing them needs dedicated handling of the embedding boundary, which this library
# doesn't attempt. Callers should fall back to whole-file context for those.
DEFINITION_NODE_TYPES: dict[str, set[str]] = {
    "csharp": {
        "method_declaration", "constructor_declaration", "destructor_declaration",
        "property_declaration", "indexer_declaration", "event_declaration",
        "operator_declaration", "local_function_statement",
        "class_declaration", "struct_declaration", "interface_declaration",
    },
    "typescript": {
        "function_declaration", "method_definition", "arrow_function",
        "function_expression", "class_declaration", "interface_declaration",
        "lexical_declaration", "export_statement",
        "public_field_definition", "field_definition", "property_signature",
    },
    "tsx": {
        "function_declaration", "method_definition", "arrow_function",
        "function_expression", "class_declaration", "interface_declaration",
        "lexical_declaration", "export_statement",
        "public_field_definition", "field_definition", "property_signature",
    },
    "javascript": {
        "function_declaration", "method_definition", "arrow_function",
        "function_expression", "class_declaration",
        "lexical_declaration", "export_statement",
        "public_field_definition", "field_definition",
    },
}

# Node types that count as "containers" (last-resort escalation target) rather than
# "members" (preferred narrowing target) within a language's definition set. See
# narrowing.find_enclosing_node for why this distinction exists.
CONTAINER_NODE_TYPES: set[str] = {"class_declaration", "struct_declaration", "interface_declaration"}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(DEFINITION_NODE_TYPES.keys())


def language_for_path(path: str) -> str | None:
    """Return the registered language for a file path's extension, or None if unsupported."""
    import os

    ext = os.path.splitext(path)[1].lower()
    return EXTENSION_LANGUAGE_MAP.get(ext)


def get_parser(language: str):
    """Thin wrapper around tree-sitter-language-pack's get_parser, isolated here so the
    rest of the library doesn't take a hard dependency on that specific package name."""
    try:
        from tree_sitter_language_pack import get_parser as _get_parser
    except ImportError as exc:
        raise ImportError(
            "ast_context_narrowing requires tree-sitter + tree-sitter-language-pack. "
            "Install with: pip install ast-context-narrowing[parsers]"
        ) from exc
    return _get_parser(language)
