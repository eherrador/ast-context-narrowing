# ast-context-narrowing

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.22905152-blue.svg)](https://doi.org/10.5281/zenodo.22905152)

Narrow a code diff to the structural definition it touched — a method, function,
class, or field — instead of the raw diff hunk or the whole file.

## The problem

Tools that turn code diffs into training data or review context (LLM fine-tuning
pipelines, code-review bots, changelog generators, RAG indexes over commit history)
have to decide how much surrounding code to keep around each change. Two common
choices, both flawed:

- **The raw diff hunk.** A unified diff pads the actual `+`/`-` lines with a few lines
  of context on each side — not enough to recover a method's signature, its enclosing
  class, or a preceding doc comment, and *too much* for short methods, where the
  padding spills into unrelated neighboring code.
- **The whole file.** Drowns a one-line fix in hundreds of unrelated lines, which is
  especially costly for LLM fine-tuning: the model spends most of its attention budget
  on code that never changed, and the signal-to-noise ratio of the training signal
  drops accordingly.

**This library takes a third approach**: parse the file with [Tree-sitter](https://tree-sitter.github.io/tree-sitter/)
and walk the AST from the changed byte range upward until it finds the smallest
enclosing *definition* node — the method/function/class/field that was actually
touched — and returns just that.

## Install

```bash
pip install ast-context-narrowing[parsers]
```

(The `[parsers]` extra pulls in `tree-sitter` + `tree-sitter-language-pack`. The core
narrowing logic has no hard dependency on them — see [`languages.py`](ast_context_narrowing/languages.py)
if you want to wire up a different Tree-sitter binding.)

## Quick start

```python
from ast_context_narrowing import narrow_diff_context

result = narrow_diff_context(
    language="csharp",
    before_source=before_bytes,   # file content before the change
    after_source=after_bytes,     # file content after the change
    unified_diff=patch_text,      # e.g. from `git diff` or a GitHub PR "files" API response
)

if result.scope == "structural":
    print(result.context)   # the enclosing method/class, before
    print(result.output)    # the enclosing method/class, after
else:
    # no definition node contained the change (e.g. a top-level statement, or the
    # language isn't registered) -- fall back to whatever your caller needs, e.g.
    # the whole file.
    ...
```

Runnable version: [`examples/basic_usage.py`](examples/basic_usage.py).

## Supported languages

C#, TypeScript, TSX, JavaScript (see [`languages.py`](ast_context_narrowing/languages.py)
for the exact node-type sets used per language). Hybrid-grammar languages (Razor
`.cshtml`, Vue SFCs, etc.) aren't supported — narrowing them needs dedicated handling
of the embedding boundary between host and guest language, which this library doesn't
attempt. Adding a new language means adding an entry to `DEFINITION_NODE_TYPES` with
the Tree-sitter node types that count as a "definition" in that grammar; the walking
logic itself is language-agnostic.

## Narrowing rules

The core algorithm — "find the smallest node containing this byte range whose type is
in a per-language allowlist" — sounds simple, but real diffs hit several edge cases
that silently degrade it back into whole-class or whole-file context if left
unhandled. Each rule below exists because of one:

- **Anchor to touched lines, not the padded hunk span.** A unified diff hunk includes
  ~3 lines of context on each side of the actual change. For a short method (typical
  of unit tests), that padding alone can span past the method's boundaries into a
  neighboring one, so *no single node* contains the full padded span and the search
  falsely escalates to the enclosing class. Anchoring to just the `+`/`-` lines keeps
  the range inside the definition that actually changed.
- **Trim trailing statement terminators.** Some grammars (JS/TS) model a field or
  variable declaration without its trailing `;` as a separate sibling token. A diff
  line naturally includes the `;`, so without trimming it, an otherwise-exact
  single-line field edit looks like it overruns the field node by one byte, and the
  search climbs to the enclosing class for what should be a one-line match.
  
- **Skip past a preceding doc comment.** A JSDoc block or C# `///` summary is a
  separate sibling node, not part of the method's own span. When a diff rewrites both
  the doc comment and the method body together, the raw touched-range starts inside
  the comment, so no single method node contains the whole thing. Skipping leading
  comment nodes before searching keeps the range inside the method.
- **Treat a pure insertion at a blank line as a zero-width point, not a dropped hunk.**
  A diff hunk with no `-` lines anchors to wherever the insertion happened in the
  other revision. If that line happens to be blank (a common separator between
  statements), naively trimming whitespace from the range collapses it to nothing —
  which, left unhandled, drops the hunk's contribution to the context entirely. A
  zero-width point still works for the containment check that finds the enclosing
  node.
- **Container types (class/struct/interface) are a last resort, not a first-class
  target.** A floating comment or blank-line insertion with no enclosing
  method/property/field around it is *technically* contained by the whole class — but
  escalating straight there turns a one-line change into "the entire class as
  context." Trying the smallest enclosing node of *any* type first, and only falling
  back to the class/struct/interface if nothing smaller exists, keeps context tight
  for the common case where a smaller node (even an unnamed one) is available.
- **`export class Foo { ... }` is a container, even though `export_statement` is
  itself a definition type.** TS/JS/TSX register `export_statement` as a definition
  (for bare `export function foo()` / `export const x = ...`, where that IS the
  smallest sensible unit). But when an `export_statement` *wraps* a class/struct/
  interface, it needs the same last-resort demotion as the container itself — without
  this, `export class Foo` is technically a "bigger container" that slips past the
  container check and a floating comment inside it escalates to the whole exported
  class.

Each rule has a dedicated regression test in [`tests/test_narrowing.py`](tests/test_narrowing.py),
using synthetic minimal reproductions.

## Running the tests

```bash
pip install -e .[dev]
pytest tests/ -v
```

## Evaluation

Run against 175 real file changes from 37 merged pull requests in
[`nestjs/nest`](https://github.com/nestjs/nest) (a large, active, real-world
TypeScript project), narrowing resolved a definition node for every change, with a
**median 71.8% reduction** in line count relative to whole-file context (mean
53.2%; the distribution is bimodal — see the paper for why). Reproduce with:

```bash
python evaluation/run_public_repo_eval.py --owner nestjs --repo nest --max-prs 100
```

Full methodology, honest discussion of what the 100% resolution rate does and
doesn't mean, and per-file raw results: [`paper/ast-context-narrowing.md`](paper/ast-context-narrowing.md)
(Section 4) / [`evaluation/results/nestjs_nest.json`](evaluation/results/nestjs_nest.json).

## Further reading

- [`paper/ast-context-narrowing.md`](paper/ast-context-narrowing.md) — full technical write-up (motivation, method, evaluation, related work, limitations).
- [`blog/ast-context-narrowing-blog.md`](blog/ast-context-narrowing-blog.md) — shorter, less formal version of the same content.

## Origin

Extracted and generalized from the data-curation pipeline of a small-language-model
fine-tuning proof-of-concept, where naive whole-file context was diluting the training
signal from real pull-request review history. This library is the reusable core of
that narrowing logic, with no dependency on any specific codebase, VCS host, or
dataset format.

## License

MIT — see [LICENSE](LICENSE).
