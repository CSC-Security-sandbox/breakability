#!/usr/bin/env python3
"""Break-class router — the false-green guard for the differential probe.

A minimal call-site harness can only OBSERVE a behavioral break that manifests
at (or very near) the call: a changed default value, return value, error, output
format, or signature. It is structurally BLIND to breaks that only manifest under
runtime state, load, time, concurrency or resource pressure (cardinality limits,
memory growth, retry/backoff timing, pool exhaustion, races). Probing those classes
diffs to nothing and emits a confident FALSE GREEN on exactly the dangerous cases.

So before probing we classify the maintainer-declared break:
  - CALL_OBSERVABLE   -> safe to probe (the probe is competent here).
  - NOT_OBSERVABLE    -> never probe; route to an honest Medium + targeted note.
  - AMBIGUOUS         -> conservative: treat as not-probe-able (Medium / AI-reasoning).

Rule: NOT_OBSERVABLE markers WIN over CALL_OBSERVABLE markers. And a changed
DEFAULT/CONFIG/OPTION/KNOB is treated as NOT_OBSERVABLE *unless* the same bullet also
names an explicit call-local dimension (return value/error/format/signature). So
"default cardinality limit changed 0 -> 2000" routes NOT_OBSERVABLE (load dimension),
and "default request budget 100 -> 50" ALSO routes NOT_OBSERVABLE (changed default with
no call-local evidence) -- a minimal probe would false-green on both. Only
"default return value is now 0 instead of -1" stays CALL_OBSERVABLE. This is
product-agnostic (keyword heuristics over changelog prose, no repo-specific
assumptions); the conservative inversion ensures we never optimistically probe an
unknown changed default.
"""
import re

# Markers for breaks that only surface under runtime state/load/time/concurrency/
# resource pressure. A minimal call-site probe CANNOT reproduce these, so their
# presence forces NOT_OBSERVABLE regardless of any call-observable marker.
NOT_OBSERVABLE_MARKERS = [
    # cardinality / volume / scaling
    "cardinalit", "high cardinalit", "series limit", "label limit", "too many",
    "at scale", "under load", "high load", "scaling",
    "throughput", "qps", "rps",
    # memory / resource (avoid bare common config-field nouns -> collision)
    "memory", "mem usage", "heap", "leak", "allocation", "garbage collect",
    "buffer pool", "backpressure", "connection pool", "pool exhaust",
    "file descriptor", "resource exhaust", "out of memory", "oom",
    # time / latency / scheduling (use DIMENSION phrases, not field names like
    # "timeout"/"interval"/"ttl" which are observable defaults when they change)
    "latency", "slower", "faster", "performance", "timing", "times out",
    "deadline exceeded", "retry", "retries", "backoff", "rate limit",
    "ratelimit", "rate-limit", "throttl", "polling interval", "scrape interval",
    "heartbeat", "keepalive", "keep-alive", "eviction", "evict",
    "over time", "long-running", "accumulat", "drift",
    # concurrency
    "concurren", "goroutine", "thread", "race", "data race", "deadlock",
    "lock contention", "contention", "mutex", "atomic", "parallel",
    "synchroniz",
    # integration / external / stateful lifecycle
    "reconnect", "failover", "graceful shutdown", "shutdown", "lifecycle",
    "stateful", "session state", "connection state", "checkpoint",
]

# Markers for breaks observable at/near the call: changed return value/type,
# error/validation, output format, signature/parameters. NOTE: generic "changed
# default" phrases are deliberately NOT here -- a changed default is only probe-safe
# when paired with one of these explicit call-local dimensions (see classify_bullet).
CALL_OBSERVABLE_MARKERS = [
    "now returns", "returns a", "returns an", "return value", "return type",
    "no longer returns", "returns nil", "returns empty", "returns error",
    "returns an error", "now an error", "raises", "panics", "throws",
    "validation", "validates", "now rejects", "rejects", "invalid",
    "format", "formatting", "serializ", "deserializ", "marshal", "unmarshal",
    "json output", "output format", "encoding", "decode", "parse",
    "signature", "parameter", "argument", "now takes", "added a parameter",
    "removed the parameter", "renamed", "removed the", "has been removed",
    "no longer accepts", "case-sensitive", "case sensitive", "case-insensitive",
    "rounding", "truncat", "escap", "quoting", "trailing", "leading",
    "zero value", "empty string", "nil instead",
]

# Phrases that signal a changed DEFAULT / CONFIG / OPTION / KNOB. On their own these
# are NOT probe-safe: a minimal call-site harness constructs fine at both versions and
# the break (if any) surfaces later under runtime data/load/state. A changed default is
# only routed to a probe when the SAME bullet also carries explicit call-local evidence
# (a CALL_OBSERVABLE_MARKER, e.g. "default return value is now ..."). Everything else --
# "default request budget 100 -> 50", "changed the default mode", "new default policy" --
# routes NOT_OBSERVABLE so the reasoning oracle grades it instead of a probe that would
# false-green. This is the conservative inversion: unknown changed-defaults are NOT
# assumed observable.
CONFIG_DEFAULT_MARKERS = [
    "default", "defaults", "configuration", "config", "setting", "settings",
    "option", "knob", "flag", "env var", "environment variable", "policy", "mode",
    "behavior", "behaviour",
]

CALL_OBSERVABLE = "call_observable"
NOT_OBSERVABLE = "not_observable"
AMBIGUOUS = "ambiguous"

# Nouns that name a runtime RESOURCE/CAPACITY or TIMING knob. A changed *default*
# for one of these (e.g. "default limit 0 -> 2000", "default timeout 10s -> 30s") is
# not safe to probe: its break manifests under volume/load/time, not at construction.
# Route to NOT_OBSERVABLE so the reasoning oracle (release-notes + usage) grades it
# instead of a probe that would false-green. Safe to be inclusive: with a real
# not_observable oracle, mis-routing here only costs a reasoning call, never a false green.
RESOURCE_DEFAULT_NOUNS = [
    # capacity / resource
    "limit", "maximum", "max num", "max number", "capacity", "quota",
    "threshold", "buffer", "pool", "queue", "depth", "window size",
    "connection", "concurrency", "parallelism", "batch size", "cache size",
    "series", "cardinalit",
    # timing / duration
    "timeout", "deadline", "interval", "ttl", "duration", "delay",
    "backoff", "period", "expiry", "expiration", "retry",
]


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower())


def _hits(text, markers):
    return sorted({m.strip() for m in markers if m in text})


def classify_bullet(bullet):
    """Classify ONE changelog bullet. Returns (klass, matched_markers).

    Order of precedence (conservative; never optimistically probe a config change):
      1. runtime state/load/time/concurrency dimension present -> NOT_OBSERVABLE.
      2. changed DEFAULT/CONFIG/OPTION/KNOB present:
           - WITH explicit call-local evidence (return/error/format/signature) -> CALL_OBSERVABLE.
           - otherwise (unknown/resource/timing/state default) -> NOT_OBSERVABLE.
      3. explicit call-local evidence (no config change) -> CALL_OBSERVABLE.
      4. else -> AMBIGUOUS (treated as not-probe-able by the driver -> reasoning).
    """
    t = _norm(bullet)
    if not t:
        return AMBIGUOUS, []
    not_obs = _hits(t, NOT_OBSERVABLE_MARKERS)
    if not_obs:
        # Load/state/time/concurrency dimension present -> probe is blind. Route away.
        return NOT_OBSERVABLE, not_obs
    call_obs = _hits(t, CALL_OBSERVABLE_MARKERS)
    is_config = any(c in t for c in CONFIG_DEFAULT_MARKERS)
    if is_config:
        # A changed default/config/knob is probe-safe ONLY when the SAME bullet names an
        # explicit call-local dimension (e.g. "default return value is now 0",
        # "default formatter output changed"). Without that, a minimal probe is
        # structurally blind (the break surfaces under runtime data/load/state), so we
        # route to the reasoning oracle rather than false-green. This closes the
        # "changed default <unlisted noun>" hole (e.g. "default request budget 100 -> 50").
        if call_obs:
            return CALL_OBSERVABLE, call_obs + ["config-default+call-local"]
        res = [n for n in RESOURCE_DEFAULT_NOUNS if n in t]
        markers = (["resource-default-change"] + res) if res else ["config-default-unverified"]
        return NOT_OBSERVABLE, markers
    if call_obs:
        return CALL_OBSERVABLE, call_obs
    return AMBIGUOUS, []


def classify_break(bullets):
    """Classify a PR's declared break across its bullets (conservative aggregate).

    Returns a dict:
      { "class": call_observable|not_observable|ambiguous,
        "probe_recommended": bool,
        "observable_bullet": "<the bullet to probe, if any>",
        "reason": "<one-line why>",
        "markers": [ ... ] }

    Precedence: if ANY bullet is NOT_OBSERVABLE -> the PR is NOT_OBSERVABLE (a
    load/state break anywhere means a minimal probe cannot be trusted to clear it).
    Else if ANY bullet is CALL_OBSERVABLE -> probe that bullet. Else AMBIGUOUS.
    """
    if not bullets:
        return {
            "class": AMBIGUOUS, "probe_recommended": False,
            "observable_bullet": "", "reason": "no changelog bullets to classify",
            "markers": [],
        }
    per = [(b, *classify_bullet(b)) for b in bullets if isinstance(b, str) and b.strip()]
    not_obs = [(b, m) for (b, k, m) in per if k == NOT_OBSERVABLE]
    if not_obs:
        b, m = not_obs[0]
        return {
            "class": NOT_OBSERVABLE, "probe_recommended": False,
            "observable_bullet": "",
            "reason": "break is state/load/time/concurrency-dependent "
                      "(not reproducible from a minimal call-site probe)",
            "markers": m,
        }
    call_obs = [(b, m) for (b, k, m) in per if k == CALL_OBSERVABLE]
    if call_obs:
        b, m = call_obs[0]
        return {
            "class": CALL_OBSERVABLE, "probe_recommended": True,
            "observable_bullet": b,
            "reason": "break is observable at/near the call site "
                      "(default/return/error/format/signature)",
            "markers": m,
        }
    return {
        "class": AMBIGUOUS, "probe_recommended": False,
        "observable_bullet": "",
        "reason": "break dimension unclear from changelog prose",
        "markers": [],
    }


if __name__ == "__main__":
    import json
    import sys
    bullets = json.load(sys.stdin) if not sys.stdin.isatty() else sys.argv[1:]
    print(json.dumps(classify_break(bullets), indent=2))
