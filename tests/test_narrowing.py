"""Regression tests for the AST-narrowing rules.

Each test formalizes one real failure mode found while narrowing a large real-world
diff corpus down to structural context: short-method hunk spans false-escalating to
the enclosing class, trailing semicolons on single-line field edits, doc-comment
blocks preceding the changed member, blank-line-only insertions, and floating
comments/exported classes escalating past the smallest enclosing node. All source
snippets below are synthetic/minimal reproductions, not excerpted from any real
codebase.
"""

from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ast_context_narrowing.diff import line_range_to_byte_range, parse_diff_hunks
from ast_context_narrowing.languages import DEFINITION_NODE_TYPES, get_parser
from ast_context_narrowing.narrowing import (
    collect_comment_nodes,
    find_enclosing_node,
    skip_leading_comments,
)
from ast_context_narrowing.api import narrow_diff_context


def make_patch(old_start, old_len, new_start, new_len, body_lines):
    header = f"@@ -{old_start},{old_len} +{new_start},{new_len} @@"
    return "\n".join([header] + body_lines)


# ---------------------------------------------------------------------------
# parse_diff_hunks / line_range_to_byte_range
# ---------------------------------------------------------------------------

def test_parse_diff_hunks_anchors_to_touched_lines_only():
    patch_text = make_patch(10, 7, 10, 7, [
        " unchanged before",
        "-old body line",
        "+new body line",
        " unchanged after",
    ])
    hunks = parse_diff_hunks(patch_text)
    assert len(hunks) == 1
    assert hunks[0]["old_start"] == 11  # the '-' line only, not the padding
    assert hunks[0]["new_start"] == 11


def test_field_edit_trailing_semicolon_not_part_of_node_span():
    source = b"class Foo {\n  private x = 1;\n}\n"
    start_byte, end_byte = line_range_to_byte_range(source, 2, 1)
    assert source[start_byte:end_byte] == b"private x = 1"  # ';' trimmed


# ---------------------------------------------------------------------------
# Doc-comment skipping: a diff that rewrites both a doc comment and the method
# body it precedes shouldn't escalate to the enclosing class.
# ---------------------------------------------------------------------------

TS_SOURCE = b"""class Foo {
  /**
   * @description does the thing
   */
  withRecordType(x: number): number {
    return x + 1;
  }
}
"""

CS_SOURCE = b"""public class Foo
{
    /// <summary>
    /// Does the thing.
    /// </summary>
    public int WithRecordType(int x)
    {
        return x + 1;
    }
}
"""


def _line_range_covering(source: bytes, substring: bytes):
    """1-indexed (start_line, line_count) covering every line substring appears on."""
    lines = source.split(b"\n")
    start_idx = next(i for i, line in enumerate(lines) if substring.splitlines()[0] in line)
    return start_idx + 1, len(substring.splitlines())


def test_typescript_jsdoc_plus_method_body_narrows_to_method_not_class():
    parser = get_parser("typescript")
    tree = parser.parse(TS_SOURCE)

    start_line, _ = _line_range_covering(TS_SOURCE, b"@description")
    end_line = TS_SOURCE.split(b"\n").index(b"  }")
    line_count = end_line - start_line + 2

    start_byte, end_byte = line_range_to_byte_range(TS_SOURCE, start_line, line_count)
    comment_nodes = collect_comment_nodes(tree.root_node)
    start_byte = skip_leading_comments(comment_nodes, TS_SOURCE, start_byte, end_byte)

    node = find_enclosing_node(tree.root_node, start_byte, end_byte, DEFINITION_NODE_TYPES["typescript"])
    assert node.type == "method_definition", (
        f"expected narrowing to the method, got {node.type!r} "
        f"({TS_SOURCE[node.start_byte:node.end_byte][:30]!r}...)"
    )


def test_csharp_xmldoc_plus_method_body_narrows_to_method_not_class():
    parser = get_parser("csharp")
    tree = parser.parse(CS_SOURCE)

    start_line, _ = _line_range_covering(CS_SOURCE, b"<summary>")
    end_line = CS_SOURCE.split(b"\n").index(b"    }")
    line_count = end_line - start_line + 2

    start_byte, end_byte = line_range_to_byte_range(CS_SOURCE, start_line, line_count)
    comment_nodes = collect_comment_nodes(tree.root_node)
    start_byte = skip_leading_comments(comment_nodes, CS_SOURCE, start_byte, end_byte)

    node = find_enclosing_node(tree.root_node, start_byte, end_byte, DEFINITION_NODE_TYPES["csharp"])
    assert node.type == "method_declaration", (
        f"expected narrowing to the method, got {node.type!r} "
        f"({CS_SOURCE[node.start_byte:node.end_byte][:30]!r}...)"
    )


def test_comment_only_hunk_is_left_unskipped():
    """A hunk that only touches comment lines shouldn't be force-advanced past end_byte."""
    parser = get_parser("typescript")
    tree = parser.parse(TS_SOURCE)
    comment_nodes = collect_comment_nodes(tree.root_node)

    start_line, line_count = _line_range_covering(TS_SOURCE, b"@description")
    start_byte, end_byte = line_range_to_byte_range(TS_SOURCE, start_line, 1)
    result = skip_leading_comments(comment_nodes, TS_SOURCE, start_byte, end_byte)
    assert result == start_byte  # unchanged: nothing but comment in this range


# ---------------------------------------------------------------------------
# Blank-line pure-insertion anchor
# ---------------------------------------------------------------------------

BLANK_LINE_SOURCE = b"""class Foo {
  method1() {
    doA();

    doB();
  }
}
"""


def test_blank_line_insertion_anchor_is_a_point_not_dropped():
    # old_len=1 anchored at the blank line between doA() and doB() -- the shape a pure
    # insertion produces when the line at the insertion point happens to be blank.
    blank_line_no = BLANK_LINE_SOURCE.split(b"\n").index(b"") + 1
    start_byte, end_byte = line_range_to_byte_range(BLANK_LINE_SOURCE, blank_line_no, 1)
    assert start_byte == end_byte, "expected a zero-width anchor point, not a dropped/empty range"

    parser = get_parser("typescript")
    tree = parser.parse(BLANK_LINE_SOURCE)
    node = find_enclosing_node(tree.root_node, start_byte, end_byte, DEFINITION_NODE_TYPES["typescript"])
    assert node.type == "method_definition"  # not None, not the whole class


# ---------------------------------------------------------------------------
# Container-type (class/struct/interface) as last resort, not tier-1
# ---------------------------------------------------------------------------

FLOATING_COMMENT_SOURCE = b"""class Foo {
  // floating top-level note, not attached to any member
  method1() { return 1; }
}
"""


def test_floating_comment_prefers_small_node_over_whole_class():
    parser = get_parser("typescript")
    tree = parser.parse(FLOATING_COMMENT_SOURCE)

    comment_start = FLOATING_COMMENT_SOURCE.index(b"//")
    comment_end = FLOATING_COMMENT_SOURCE.index(b"\n", comment_start)

    node = find_enclosing_node(
        tree.root_node, comment_start, comment_end, DEFINITION_NODE_TYPES["typescript"]
    )
    assert node.type != "class_declaration", "should prefer the small comment node over escalating to the class"


EXPORTED_CLASS_SOURCE = b"""export class Foo {

  // --- signals ---

  public readonly ready = signal(false);

  /** doc for bar */
  public readonly bar = signal(false);

  method1() { return 1; }
}
"""


def test_floating_comment_in_exported_class_prefers_small_node_over_export_statement():
    """
    `export class Foo {}` parses as an export_statement wrapping a class_declaration.
    Demoting class_declaration alone isn't enough -- export_statement is itself in
    DEFINITION_NODE_TYPES (for bare `export function`/`export const`), so a floating
    comment/blank-line insertion point with no smaller member around it was escalating
    to the *whole exported class* instead.
    """
    parser = get_parser("typescript")
    tree = parser.parse(EXPORTED_CLASS_SOURCE)

    comment_start = EXPORTED_CLASS_SOURCE.index(b"// --- signals")
    comment_end = EXPORTED_CLASS_SOURCE.index(b"\n", comment_start)

    node = find_enclosing_node(
        tree.root_node, comment_start, comment_end, DEFINITION_NODE_TYPES["typescript"]
    )
    assert node.type not in ("export_statement", "class_declaration"), (
        f"expected a small node, got {node.type!r} size={node.end_byte - node.start_byte}"
    )


# ---------------------------------------------------------------------------
# End-to-end narrow_diff_context integration
# ---------------------------------------------------------------------------

def test_narrow_diff_context_narrows_jsdoc_plus_method_edit():
    patch_text = make_patch(2, 6, 2, 6, [
        " class Foo {",
        "-  /**",
        "-   * @description does the thing",
        "+  /**",
        "+   * @description does the updated thing",
        "   */",
        "  withRecordType(x: number): number {",
        "-    return x + 1;",
        "+    return x + 2;",
        "  }",
        " }",
    ])

    before_source = TS_SOURCE
    after_source = TS_SOURCE.replace(b"does the thing", b"does the updated thing").replace(b"x + 1", b"x + 2")

    result = narrow_diff_context("typescript", before_source, after_source, patch_text)

    assert result.scope == "structural"
    assert "class Foo" not in result.context  # narrowed to the method, not the whole class
    assert "withRecordType" in result.context
    assert "withRecordType" in result.output


def test_narrow_diff_context_returns_unavailable_for_unregistered_language():
    result = narrow_diff_context("cobol", b"", b"", "")
    assert result.scope == "unavailable"
    assert result.context is None
    assert result.output is None
