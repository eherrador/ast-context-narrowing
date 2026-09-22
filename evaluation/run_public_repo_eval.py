"""
Reproducible evaluation: run ast_context_narrowing on real merged-PR diffs from a
public GitHub repository, and measure how much smaller the narrowed context is than
the whole file, plus how often narrowing succeeds at all.

Uses the GitHub CLI (`gh api`) for all API access, so it works with whatever `gh`
account is already authenticated on the machine running it -- no token handling in
this script. Only reads PUBLIC repository data.

Usage:
    pip install -e .[dev]
    python evaluation/run_public_repo_eval.py --owner nestjs --repo nest --max-prs 100

Writes evaluation/results/<owner>_<repo>.json with per-file records and prints the
aggregate summary used in the paper's evaluation section.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ast_context_narrowing import language_for_path, narrow_diff_context

MAX_FILE_BYTES = 60_000  # skip very large files -- not representative of a "focused" diff


def gh_api(path: str) -> object:
    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def gh_api_paginated(path: str, pages: int) -> list:
    items = []
    for page in range(1, pages + 1):
        sep = "&" if "?" in path else "?"
        batch = gh_api(f"{path}{sep}page={page}")
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
    return items


def fetch_file_content(owner: str, repo: str, path: str, sha: str) -> bytes | None:
    encoded_path = path.replace(" ", "%20")
    data = gh_api(f"repos/{owner}/{repo}/contents/{encoded_path}?ref={sha}")
    if not data or "content" not in data:
        return None
    try:
        return base64.b64decode(data["content"])
    except Exception:
        return None


def evaluate(owner: str, repo: str, max_prs: int) -> list[dict]:
    pages = max(1, (max_prs + 99) // 100)
    prs = gh_api_paginated(
        f"repos/{owner}/{repo}/pulls?state=closed&per_page=100&sort=updated&direction=desc",
        pages,
    )
    prs = [pr for pr in prs if pr.get("merged_at")][:max_prs]
    print(f"Evaluating {len(prs)} merged PRs from {owner}/{repo}...")

    records = []
    for i, pr in enumerate(prs):
        number = pr["number"]
        base_sha = pr["base"]["sha"]
        head_sha = pr["head"]["sha"]

        if (i + 1) % 10 == 0:
            print(f"  PR {i + 1}/{len(prs)} (#{number})...")

        files = gh_api(f"repos/{owner}/{repo}/pulls/{number}/files?per_page=100") or []
        for f in files:
            filename = f.get("filename", "")
            language = language_for_path(filename)
            if not language:
                continue
            if f.get("status") not in ("modified",):
                continue  # added/removed files have no meaningful "narrow the diff" case
            patch = f.get("patch")
            if not patch:
                continue
            if f.get("changes", 0) > 200:
                continue  # skip sprawling diffs, not representative of a focused change

            before = fetch_file_content(owner, repo, filename, base_sha)
            after = fetch_file_content(owner, repo, filename, head_sha)
            if before is None or after is None:
                continue
            if len(before) > MAX_FILE_BYTES or len(after) > MAX_FILE_BYTES:
                continue

            try:
                result = narrow_diff_context(language, before, after, patch)
            except Exception as exc:
                records.append({
                    "pr": number, "file": filename, "language": language,
                    "scope": "error", "error": str(exc),
                })
                continue

            record = {
                "pr": number,
                "file": filename,
                "language": language,
                "scope": result.scope,
                "whole_file_lines": len(after.splitlines()),
                "whole_file_bytes": len(after),
            }
            if result.scope == "structural":
                record["narrowed_output_lines"] = len(result.output.splitlines())
                record["narrowed_output_bytes"] = len(result.output.encode("utf-8"))
            records.append(record)

    return records


def summarize(records: list[dict]) -> dict:
    total = len(records)
    structural = [r for r in records if r["scope"] == "structural"]
    unavailable = [r for r in records if r["scope"] == "unavailable"]
    errors = [r for r in records if r["scope"] == "error"]

    line_reductions = [
        1 - (r["narrowed_output_lines"] / r["whole_file_lines"])
        for r in structural
        if r["whole_file_lines"] > 0
    ]
    byte_reductions = [
        1 - (r["narrowed_output_bytes"] / r["whole_file_bytes"])
        for r in structural
        if r["whole_file_bytes"] > 0
    ]

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    def median(xs):
        s = sorted(xs)
        n = len(s)
        if n == 0:
            return 0.0
        mid = n // 2
        return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2

    by_language = {}
    for r in records:
        by_language.setdefault(r["language"], {"total": 0, "structural": 0})
        by_language[r["language"]]["total"] += 1
        if r["scope"] == "structural":
            by_language[r["language"]]["structural"] += 1

    return {
        "total_file_changes_evaluated": total,
        "structural_narrowing_succeeded": len(structural),
        "structural_narrowing_rate": len(structural) / total if total else 0.0,
        "unavailable": len(unavailable),
        "errors": len(errors),
        "mean_line_reduction": mean(line_reductions),
        "median_line_reduction": median(line_reductions),
        "mean_byte_reduction": mean(byte_reductions),
        "median_byte_reduction": median(byte_reductions),
        "by_language": by_language,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--max-prs", type=int, default=100)
    args = parser.parse_args()

    records = evaluate(args.owner, args.repo, args.max_prs)
    summary = summarize(records)

    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{args.owner}_{args.repo}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "records": records}, f, indent=2)

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
