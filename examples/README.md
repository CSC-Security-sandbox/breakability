# Breakability Analysis Output Templates

**Gold standard examples** from production runs showing all required sections.

## Gold Standard PR Comments

**pr-208-gold-standard.md** - Complete example with all 13 mandatory sections:
- Header (verdict, confidence, priority)
- Signals checked table
- Build analysis
- Merge risk
- CVE details (if applicable)
- Verification level
- Reachability context
- Files importing
- Dependency resolution
- Build/test output
- Changelog signals
- API diff
- Advisory notice

**vcp-pr-23-gold-standard.md** - VCP example with security fix + behavioral breaking change

## Required Sections

All PR comments MUST include these 13 sections. Missing sections = incomplete analysis.

Use these as reference for any deviations from standard.
