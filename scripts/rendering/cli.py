"""
rendering.cli — CLI entry point for rendering breakability PR comments.

Reads build-results.json and writes per-PR Markdown comment files (or stdout).
"""
import json
import sys
from rendering.renderer import render_pr_comment

__all__ = [
    "main",
]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Render breakability analysis PR comments")
    parser.add_argument("build_results", help="Path to build-results.json")
    parser.add_argument("--pr", type=str, help="Render only specific PR number")
    parser.add_argument("--stdout", action="store_true", help="Write to stdout instead of files")
    args = parser.parse_args()

    with open(args.build_results) as f:
        data = json.load(f)

    prs_dict = data.get("prs", {})
    results_array = data.get("results", [])

    if results_array:
        results = results_array
    elif prs_dict:
        results = []
        for pr_num_str, pr_data in prs_dict.items():
            if isinstance(pr_data, dict):
                pr_data.setdefault("pr_num", pr_num_str)
                results.append(pr_data)
    else:
        print("No results found in build-results.json (checked 'prs' dict and 'results' array)", file=sys.stderr)
        sys.exit(1)

    cross_deps = data.get("cross_pr_deps") or []

    if args.pr:
        results = [pr for pr in results if str(pr.get("pr_num")) == args.pr]
        if not results:
            print(f"PR #{args.pr} not found in results", file=sys.stderr)
            sys.exit(1)

    for pr in results:
        pr_num = pr.get("pr_num")
        if not pr_num:
            continue

        comment = render_pr_comment(pr, cross_deps=cross_deps)

        if args.stdout:
            print(comment)
        else:
            output_file = f"/tmp/pr-{pr_num}-comment.md"
            with open(output_file, "w") as f:
                f.write(comment)
            print(f"✅ Rendered PR #{pr_num} comment to {output_file}")
