"""Core AST-narrowing logic: given a byte range touched by a change, find the smallest
enclosing structural definition (method, function, class, field, ...) around it, and
assemble the narrowed context for a whole set of changed regions in a file.
"""

from __future__ import annotations

from .languages import CONTAINER_NODE_TYPES


def find_enclosing_node(root_node, start_byte: int, end_byte: int, definition_types: set[str]):
    """
    Walk the tree to find the smallest named node whose type is in definition_types and
    that fully contains [start_byte, end_byte). Falls back to the smallest enclosing
    named node of any type if no definition-shaped node contains the range, and to None
    if nothing in the tree contains it.

    Container types (class/struct/interface) are treated as a last resort, below both
    member-shaped definitions and any smaller node of any type. Without this, a range
    with no enclosing method/property/field -- e.g. a single comment or a blank-line
    insertion point dropped at the top of a class body -- would escalate straight to
    the whole class (its only technically-true "definition" container), even when a
    much smaller node (the comment's own node, etc.) already contains it. That's the
    difference between capturing one line of context and capturing an entire
    2000-line class for a one-line change.

    `export_statement` (TS/JS/TSX) gets the same last-resort treatment when it wraps a
    container -- `export class Foo { ... }` parses as an export_statement wrapping a
    class_declaration, so without this an `export class` is a bigger container than a
    plain `class` and slips past the class/struct/interface check entirely. A bare
    `export function foo()` / `export const x = ...` (export_statement NOT wrapping a
    container) is unaffected -- it's still treated as a normal member, since that IS
    the smallest sensible unit for a top-level exported declaration.
    """
    member_types = definition_types - CONTAINER_NODE_TYPES

    def is_container_export(node):
        return node.type == "export_statement" and any(
            child.type in CONTAINER_NODE_TYPES for child in node.children
        )

    best_member = None
    best_any = None
    best_container = None

    def visit(node):
        nonlocal best_member, best_any, best_container
        if node.start_byte > start_byte or node.end_byte < end_byte:
            return

        if node.is_named:
            size = node.end_byte - node.start_byte
            if best_any is None or size < (best_any.end_byte - best_any.start_byte):
                best_any = node
            if node.type in member_types and not is_container_export(node):
                if best_member is None or size < (best_member.end_byte - best_member.start_byte):
                    best_member = node
            elif node.type in CONTAINER_NODE_TYPES or is_container_export(node):
                if best_container is None or size < (best_container.end_byte - best_container.start_byte):
                    best_container = node

        for child in node.children:
            visit(child)

    visit(root_node)

    if best_member is not None:
        return best_member
    if best_any is not None and best_any is not best_container:
        return best_any
    return best_container or best_any


def collect_comment_nodes(root_node) -> list:
    """All 'comment'-type nodes in the tree, in source order."""
    comments = []

    def visit(node):
        if node.type == "comment":
            comments.append(node)
            return  # comments have no children worth descending into
        for child in node.children:
            visit(child)

    visit(root_node)
    return comments


def skip_leading_comments(comment_nodes: list, source_bytes: bytes, start_byte: int, end_byte: int) -> int:
    """
    If start_byte falls inside a doc-comment block (JSDoc, C# '///', etc.) directly
    preceding the definition the change also touches, advance start_byte past the
    comment(s) to where the documented code begins.

    A doc comment is a separate sibling 'comment' node, not part of the definition's own
    node span (e.g. a method_declaration starts after its doc comment). When a change
    rewrites both the doc comment and the method body together, the raw touched-range
    starts inside the comment, so no single definition node contains the full range and
    find_enclosing_node has to escalate to the enclosing class. Skipping past the
    comment(s) first keeps the range inside the definition.

    Only advances when code remains after the comment(s) within [_, end_byte) -- a
    range that only touches comment lines is left as-is.
    """
    advanced = True
    while advanced:
        advanced = False
        for node in comment_nodes:
            if node.start_byte <= start_byte < node.end_byte:
                candidate = node.end_byte
                while candidate < end_byte and source_bytes[candidate:candidate + 1].isspace():
                    candidate += 1
                if candidate < end_byte:
                    start_byte = candidate
                    advanced = True
                break
    return start_byte


def merge_byte_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent (start, end) byte ranges, sorted by start."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def extract_structural_regions(
    source_bytes: bytes,
    root_node,
    byte_ranges: list[tuple[int, int]],
    definition_types: set[str],
    *,
    joiner: str = "\n\n// ...\n\n",
) -> str | None:
    """
    For each (start_byte, end_byte) range in byte_ranges, find the smallest enclosing
    definition node (method/class/...) in root_node and return the concatenated text
    of those regions, in source order, instead of the whole file. Returns None if no
    range maps to a definition node -- the caller should fall back to whole-file
    context in that case.
    """
    if not byte_ranges or not definition_types:
        return None

    comment_nodes = collect_comment_nodes(root_node)

    resolved_ranges = []
    for start_byte, end_byte in byte_ranges:
        if start_byte > end_byte:
            continue
        # start_byte == end_byte is a valid zero-width anchor point (a pure insertion
        # whose anchor line was blank); containment in find_enclosing_node still works
        # for a point.

        start_byte = skip_leading_comments(comment_nodes, source_bytes, start_byte, end_byte)

        node = find_enclosing_node(root_node, start_byte, end_byte, definition_types)
        if node is None:
            continue

        resolved_ranges.append((node.start_byte, node.end_byte))

    if not resolved_ranges:
        return None

    merged = merge_byte_ranges(resolved_ranges)
    parts = [source_bytes[start:end].decode("utf-8-sig", errors="replace") for start, end in merged]
    joined = joiner.join(parts).strip()
    return joined or None
