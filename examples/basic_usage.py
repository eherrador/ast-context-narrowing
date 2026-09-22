"""
Minimal end-to-end example: narrow a synthetic diff to its structural context.

Run with:
    pip install -e .[parsers]
    python examples/basic_usage.py
"""

from ast_context_narrowing import narrow_diff_context

BEFORE_SOURCE = b"""public class ProjectService
{
    public List<Phase> GetPhases()
    {
        return phases;
    }
}
"""

AFTER_SOURCE = b"""public class ProjectService
{
    public List<Phase> GetPhases()
    {
        if (phases == null) return new List<Phase>();
        return phases;
    }
}
"""

# A unified diff, as produced by `git diff` or a GitHub PR "files" API response --
# note the padded context lines (unchanged lines prefixed with a space) around the
# actual '+' insertion.
UNIFIED_DIFF = """@@ -1,7 +1,8 @@
 public class ProjectService
 {
     public List<Phase> GetPhases()
     {
+        if (phases == null) return new List<Phase>();
         return phases;
     }
 }
"""


def main():
    result = narrow_diff_context("csharp", BEFORE_SOURCE, AFTER_SOURCE, UNIFIED_DIFF)

    print(f"scope: {result.scope}\n")
    print("--- context (narrowed 'before') ---")
    print(result.context)
    print("\n--- output (narrowed 'after') ---")
    print(result.output)

    # Compare against what a whole-file-context approach would have included: every
    # line of the class, even though only the method (and really, only one line
    # inside it) changed. For a large file this is the difference between a training
    # record with 5 lines of signal and one with 500 lines of noise.
    print(f"\nwhole file was {len(AFTER_SOURCE.splitlines())} lines; "
          f"narrowed output is {len(result.output.splitlines())} lines")


if __name__ == "__main__":
    main()
