"""Unified-diff parsing: turn a patch into byte ranges anchored to the lines that
actually changed, not the padded context a unified diff includes around them.
"""

from __future__ import annotations

import re

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff_hunks(patch: str) -> list[dict]:
    """
    Parse a unified-diff patch string into hunks anchored to the lines that actually
    changed ('+'/'-'), NOT the full hunk span (which unified diff pads with ~3 lines of
    context on each side).

    Using the full hunk span as the anchor makes short methods/functions (e.g. typical
    unit tests) falsely resolve to their enclosing class: 3 lines of context above or
    below a 5-line test method easily spills into the previous/next method or its
    attributes, so no single definition node contains the whole span and the search
    climbs to the container. Anchoring to just the touched lines keeps the range inside
    the definition that actually changed.

    Returns a list of dicts: {"old_start", "old_len", "new_start", "new_len"} (all
    1-indexed line numbers/counts, matching unified-diff conventions).
    """
    hunks = []
    if not patch:
        return hunks

    lines = patch.splitlines()

    i = 0
    while i < len(lines):
        match = _HUNK_HEADER.match(lines[i])
        if not match:
            i += 1
            continue

        old_line = int(match.group(1))
        new_line = int(match.group(3))
        old_touched = []
        new_touched = []
        # For a pure insertion (no '-' lines), the old-file anchor is where the '+' run
        # starts, not wherever old_line ends up after the hunk's trailing context is
        # consumed. Symmetric case for a pure deletion on the new-file side.
        old_line_at_first_insertion = None
        new_line_at_first_deletion = None

        i += 1
        while i < len(lines) and not _HUNK_HEADER.match(lines[i]):
            body_line = lines[i]
            if body_line.startswith("---") or body_line.startswith("+++"):
                pass  # file-header lines, not part of the hunk body
            elif body_line.startswith("-"):
                if new_line_at_first_deletion is None:
                    new_line_at_first_deletion = new_line
                old_touched.append(old_line)
                old_line += 1
            elif body_line.startswith("+"):
                if old_line_at_first_insertion is None:
                    old_line_at_first_insertion = old_line
                new_touched.append(new_line)
                new_line += 1
            elif not body_line.startswith("\\"):  # "\ No newline at end of file"
                old_line += 1
                new_line += 1
            i += 1

        old_anchor_point = old_line_at_first_insertion if old_line_at_first_insertion is not None else old_line
        new_anchor_point = new_line_at_first_deletion if new_line_at_first_deletion is not None else new_line

        old_anchor = (min(old_touched), max(old_touched)) if old_touched else (old_anchor_point, old_anchor_point)
        new_anchor = (min(new_touched), max(new_touched)) if new_touched else (new_anchor_point, new_anchor_point)

        hunks.append({
            "old_start": old_anchor[0],
            "old_len": old_anchor[1] - old_anchor[0] + 1,
            "new_start": new_anchor[0],
            "new_len": new_anchor[1] - new_anchor[0] + 1,
        })

    return hunks


def line_range_to_byte_range(source_bytes: bytes, start_line: int, line_count: int) -> tuple[int, int]:
    """
    Convert a 1-indexed (start_line, line_count) range into a (start_byte, end_byte)
    range in source_bytes. A zero-length range (pure insertion/deletion at a boundary)
    is widened to at least one line so it still anchors to a node when walking the AST.
    """
    line_count = max(line_count, 1)
    lines = source_bytes.split(b"\n")

    line_offsets = []
    offset = 0
    for line in lines:
        line_offsets.append(offset)
        offset += len(line) + 1  # +1 for the split '\n'

    start_idx = max(start_line - 1, 0)
    if start_idx >= len(line_offsets):
        return len(source_bytes), len(source_bytes)

    end_idx = min(start_idx + line_count, len(line_offsets))
    start_byte = line_offsets[start_idx]
    end_byte = line_offsets[end_idx] if end_idx < len(line_offsets) else len(source_bytes)
    raw_end_byte = end_byte

    # Trim surrounding whitespace (indentation, trailing newlines). Tree-sitter node
    # boundaries never include it, so leaving it in would make an exact enclosing node
    # look like it doesn't fully contain the range, and the search would skip straight
    # to a much larger ancestor.
    while start_byte < end_byte and source_bytes[start_byte:start_byte + 1].isspace():
        start_byte += 1
    while end_byte > start_byte and source_bytes[end_byte - 1:end_byte].isspace():
        end_byte -= 1

    if start_byte >= end_byte:
        # The touched line(s) were entirely blank -- e.g. a pure insertion whose
        # anchor (the line at the insertion point in the other revision) happens to be
        # a blank separator line between two statements. Trimming collapsed the range
        # to nothing, which would otherwise make the caller drop the hunk entirely (no
        # context contributed, and if it was the only hunk in the file, an unnecessary
        # fallback for what could be a 2-line change). Anchor on the original line
        # boundary as a zero-width point instead: a point is still enough for
        # containment checks to locate the surrounding definition.
        point = min(raw_end_byte, len(source_bytes))
        return point, point

    # Same reasoning for a single trailing statement terminator: JS/TS grammars model
    # e.g. a class field or a variable declaration without its trailing ';' as a
    # separate sibling token. A diff line naturally includes it, so without this an
    # otherwise-exact match (a one-line field edit) looks like it overruns the field
    # node by one byte and the search climbs to the enclosing class instead.
    if end_byte > start_byte and source_bytes[end_byte - 1:end_byte] == b";":
        end_byte -= 1
        while end_byte > start_byte and source_bytes[end_byte - 1:end_byte].isspace():
            end_byte -= 1

    return start_byte, end_byte
