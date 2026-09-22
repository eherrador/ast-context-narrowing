# The whole file is too much. The diff hunk is too little.

If you've ever built a tool that has to decide "how much code do I show around this
change" — a fine-tuning pipeline turning PR history into training examples, a
code-review bot, a changelog generator — you've run into this exact fork in the
road:

- **Show the raw diff hunk.** Fast, cheap, and almost always wrong-sized: a unified
  diff pads the real change with a fixed ~3 lines of context on each side, which
  has nothing to do with where the actual method or class boundary is. For a short
  method, that padding spills into the next one. For a big method, it doesn't even
  cover the method's own signature.
- **Show the whole file.** Always technically sufficient. Also often absurd — a
  one-line null check fix doesn't need the other 480 lines of the file it lives in
  tagging along. If you're feeding this into an LLM's context window (training or
  inference), every one of those irrelevant lines is diluting your actual signal
  and inflating your token count.

There's an obvious third option that almost nobody documents properly: **parse the
file, and walk the AST from the change up to the nearest method, function, class,
or field.** Give the model — or the reviewer — exactly the structural unit that
changed. Not a padded window. Not the whole file. The actual thing.

I built this while curating a fine-tuning dataset from real pull-request history
for a small-language-model project (a proprietary codebase, so I can't share the
data — but the technique itself has nothing to do with that codebase, which is why
I pulled it out into its own thing). The idea sounds trivial — "find the smallest
node containing this range" — and the first version *was* trivial. It also
silently produced garbage on real diffs, in six distinct, specific ways, until I
found and fixed each one.

## The six ways "find the smallest enclosing node" breaks

**1. The diff's padding lies about where the change is.** A unified diff hunk
includes a few lines of unchanged context on each side of the real `+`/`-` lines.
For a 5-line test method, that padding alone can reach into the method before or
after it — so *no* method fully contains the padded range, and your search gives
up and escalates to the whole class. Fix: anchor to the actual `+`/`-` lines only,
ignore the padding.

**2. A trailing `;` isn't always part of the node.** Some grammars model a field
declaration's semicolon as a separate token, not part of the declaration itself.
A one-line field edit that includes the `;` (which it always does — that's how
lines work) looks, byte-for-byte, like it overruns the field's own AST node. Fix:
trim a trailing `;` before checking containment.

**3. A doc comment isn't part of the thing it documents.** JSDoc and C# `///`
comments are separate sibling nodes, sitting *before* the method they document —
not inside its span. Change both the comment and the method body in the same diff
(extremely common — you update the behavior, you update the doc), and your range
starts *before* the method's node even begins. Fix: if the range starts inside a
comment that's immediately followed by more of the range, skip past the comment
first.

**4. A blank-line insertion collapses to nothing.** Insert a new line at a blank
separator between two statements, and the "before" anchor point is... a blank
line. Trim whitespace off both ends of that (which you need to do for reasons 1-2
to work), and you get a negative-length range that a naive implementation just
drops — silently losing that part of the diff. Fix: when trimming collapses the
range, keep a zero-width point instead of nothing. A point still finds its
enclosing node just fine.

**5. The "smallest node" search shouldn't default to "the whole class."** A
floating comment with no adjacent method — like a section-header comment
`// --- signals ---` sitting alone in a class body — is, strictly speaking,
contained by the class. But narrowing to the entire class for a one-line comment
edit defeats the whole point. Fix: try the smallest node of *any* kind first, then
the smallest method/field/function, and only fall back to class/struct/interface
as an actual last resort.

**6. `export class Foo` is still a class, not an export.** TypeScript needs
`export_statement` in the "smallest sensible unit" list, because a bare
`export function foo() {}` genuinely is the right unit of context. But
`export class Foo { ... }` parses as an export wrapping a class — which,
without a special case, sails right past rule 5's "class is a last resort"
protection, because technically it matched the member-type list via
`export_statement`, not the container-type list. Fix: an `export_statement`
that directly wraps a container type gets treated as a container too.

Every one of these came from a real diff doing something I didn't expect the
first time I wrote the naive version. They're not hypothetical edge cases — they're
things that happen constantly in real code review history, which is exactly why a
tool built on the naive version will quietly degrade back into "basically whole-file
context, just with extra steps" without anyone noticing.

## Does it actually help?

I ran the narrowing logic against real merged pull requests from
[`nestjs/nest`](https://github.com/nestjs/nest) — a large, very active,
real-world TypeScript project, chosen specifically because it's public (so anyone
can reproduce this) and has nothing to do with the private codebase the technique
was originally built for.

Across 175 real file changes (37 merged PRs, 103 distinct files), narrowing found
a definition node every single time — 175/175. I'm not going to pretend that
number alone proves much on its own, though: TypeScript's definition list includes
module-level declarations and exports, so almost any top-level change lands
*somewhere* registered, even if "somewhere" turns out to be most of the file. The
number that actually matters is the size reduction, and it's bimodal: about 29%
of changes narrowed down to less than a tenth of the whole file (one file went
from 211 lines to 1 — a single interface member changed), and the median across
everything was a 71.8% reduction in line count. The other ~29% barely reduced at
all — and when I looked at those, they weren't narrowing failing quietly; they
were diffs that genuinely touched most of a file (a signature change applied
consistently everywhere it's used, or edits to a test's `describe` block spanning
most of the spec). In those cases, "most of the file" is the correct answer, not
a bug.

## Try it

```bash
pip install ast-context-narrowing[parsers]
```

```python
from ast_context_narrowing import narrow_diff_context

result = narrow_diff_context("typescript", before_bytes, after_bytes, unified_diff)
if result.scope == "structural":
    print(result.output)  # just the method/class/field that changed
```

MIT-licensed, no dependency beyond Tree-sitter, supports C#/TypeScript/TSX/
JavaScript today (adding a language is just registering its Tree-sitter node
types — the walking logic itself doesn't change).

Code + tests + the evaluation harness: https://github.com/eherrador/ast-context-narrowing

A more formal write-up (same content, paper format, with the full evaluation
methodology) is in the repo's `paper/` directory.
