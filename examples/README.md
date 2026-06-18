# Breakability Analysis Output Templates

**Gold standard from production:** https://github.com/CSC-Security-sandbox/ndm-breakability-test/pull/208#issuecomment-4737308189

## Files

**pr-208-gold-standard.md** - Complete example with full evidence:
- Behavioral probe (SHA256 mismatch + reproduction steps)
- Callsite reachability (file:line)
- API diff (11 changed exports)
- Changelog analysis
- AI arbiter reasoning
- Policy decision logic
- Independent verification links

**vcp-pr-23-gold-standard.md** - Security fix with behavioral breaking change

## Required Sections

All 13 sections must be present. Use PR #208 as reference implementation.
