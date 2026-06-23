#!/usr/bin/env python3
"""Generate AI verdicts for REVIEW PRs using Cursor CLI with Sonnet.

Reads build-results.json, finds all PRs with verdict=REVIEW, calls Cursor per-PR
to analyze if breaking changes are genuinely breaking or safe to downgrade.

Output: ai_verdicts.json with per-PR verdicts for reconcile_adjudication.py

Usage:
  generate_ai_verdicts.py <build-results.json> --output ai_verdicts.json [--model claude-sonnet-4.5]
"""
import argparse
import json
import sys
import os

# Import AI backend for Cursor CLI calls
try:
    from ai_backend import ask, resolve_model
except ImportError:
    # Fallback if not in path
    import subprocess
    def ask(namespace, prompt, model=None, key=None, **kwargs):
        """Fallback: call Cursor agent directly."""
        model = model or "claude-sonnet-4.5"
        try:
            result = subprocess.run(
                ["agent", "-p", "--force", "--model", model, prompt],
                capture_output=True, text=True, timeout=300
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception as e:
            print(f"⚠️ AI call failed: {e}", file=sys.stderr)
            return ""


def build_adjudication_prompt(pr):
    """Build prompt for AI arbiter to analyze a REVIEW PR."""
    pkg = pr.get("package", "unknown")
    from_ver = pr.get("from", "?")
    to_ver = pr.get("to", "?")
    
    det = pr.get("deterministic", {})
    api_changes = det.get("api_changes", 0)
    api_removed = det.get("api_removed", 0)
    changelog = det.get("changelogSignal", "unknown")
    import_files = det.get("import_files", [])
    
    probe = pr.get("behavioral_grade") or det.get("probe", {})
    same_behavior = probe.get("same_behavior", True)
    
    prompt = f"""You are an AI arbiter analyzing a dependency upgrade for breakability.

**Package:** {pkg} ({from_ver} → {to_ver})
**Current Verdict:** REVIEW (needs human review)

**Evidence:**
- API Changes: {api_changes} exports changed, {api_removed} removed
- Changelog: {changelog}
- Reachability: {"REACHED" if import_files else "NOT REACHED"} ({len(import_files)} files import this)
- Behavioral Probe: {"DIFFERENT" if not same_behavior else "SAME"} runtime behavior
- Import Files: {", ".join(import_files[:5]) if import_files else "None"}

**Your Task:**
Analyze if the breaking changes are genuinely breaking or safe to auto-merge.

Consider:
1. Are the API changes likely to affect typical usage patterns?
2. Do the import patterns suggest the breaking symbols are actually called?
3. Is the behavioral change acceptable (e.g., bug fix, internal refactor)?
4. Does the changelog indicate low-risk changes?

**Output Format (JSON):**
```json
{{
  "final_verdict": "SAFE" or "REVIEW",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "reasoning": "One-sentence explanation",
  "recommend_downgrade": true or false
}}
```

**Rules:**
- Never downgrade to SAFE if api_removed > 0 AND import_files exist
- Never downgrade if behavioral_probe shows DIFFERENT and imports exist
- Only downgrade if you have HIGH confidence the changes are non-breaking
- When uncertain, keep REVIEW (fail-safe)

Respond ONLY with the JSON, no other text.
"""
    return prompt


def parse_ai_response(response):
    """Parse AI response into verdict dict."""
    try:
        # Try to extract JSON from response
        import re
        json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            return {
                "final_verdict": data.get("final_verdict", "REVIEW"),
                "confidence": data.get("confidence", "MEDIUM"),
                "reasoning": data.get("reasoning", "AI analysis incomplete"),
                "recommend_downgrade": data.get("recommend_downgrade", False),
                "accepted": True  # Mark as trusted grounded output
            }
    except Exception:
        pass
    
    # Fallback: conservative
    return {
        "final_verdict": "REVIEW",
        "confidence": "LOW",
        "reasoning": "AI response parsing failed",
        "recommend_downgrade": False,
        "accepted": False
    }


def generate_verdicts(build_results, model="claude-sonnet-4.5"):
    """Generate AI verdicts for all REVIEW PRs."""
    results = build_results.get("results", [])
    prs = build_results.get("prs", {})
    
    verdicts = {}
    review_count = 0
    
    # Process PRs from results list
    for pr in results:
        pr_num = pr.get("pr")
        verdict = pr.get("verdict_v2", {}).get("verdict", "REVIEW")
        
        if verdict != "REVIEW":
            print(f"PR#{pr_num}: Skipping (verdict={verdict})", file=sys.stderr)
            continue
        
        review_count += 1
        print(f"PR#{pr_num}: Calling AI arbiter (model={model})...", file=sys.stderr)
        
        prompt = build_adjudication_prompt(pr)
        response = ask(
            namespace="ai-arbiter",
            prompt=prompt,
            model=model,
            key=f"pr-{pr_num}",
            timeout=180
        )
        
        if not response:
            print(f"PR#{pr_num}: AI call failed, keeping REVIEW", file=sys.stderr)
            verdicts[str(pr_num)] = {
                "final_verdict": "REVIEW",
                "confidence": "LOW",
                "reasoning": "AI call failed",
                "recommend_downgrade": False,
                "accepted": False
            }
            continue
        
        verdict_dict = parse_ai_response(response)
        verdicts[str(pr_num)] = verdict_dict
        
        action = "DOWNGRADE → SAFE" if verdict_dict.get("recommend_downgrade") else "KEEP REVIEW"
        print(f"PR#{pr_num}: {action} (confidence={verdict_dict.get('confidence')})", file=sys.stderr)
    
    print(f"\n✅ Processed {review_count} REVIEW PRs", file=sys.stderr)
    return verdicts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="Path to build-results.json")
    ap.add_argument("--output", default="ai_verdicts.json", help="Output file for verdicts")
    ap.add_argument("--model", default="claude-sonnet-4.5", help="AI model to use")
    args = ap.parse_args()
    
    # Check Cursor CLI available
    import subprocess
    try:
        subprocess.run(["agent", "--version"], capture_output=True, timeout=5)
    except Exception:
        print("::error::Cursor agent CLI not found - AI layer requires 'agent' command", file=sys.stderr)
        sys.exit(1)
    
    # Load build results
    with open(args.results) as f:
        build_results = json.load(f)
    
    # Generate verdicts
    verdicts = generate_verdicts(build_results, model=args.model)
    
    # Write output
    with open(args.output, "w") as f:
        json.dump(verdicts, f, indent=2)
    
    print(f"✅ Wrote {len(verdicts)} verdicts to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
