"""Per-(pkg,from,to,call-site) cache for probe/reasoning results."""
import hashlib
import json
import os

from .config import CACHE_DIR, PROMPT_VERSION

__all__ = ["cache_key", "cache_get", "cache_put"]


def cache_key(ctx):
    sig = "|".join([
        ctx.get("kind", "probe"),
        ctx.get("package", ""), ctx.get("from", ""), ctx.get("to", ""),
        (ctx.get("call_site") or {}).get("file", ""),
        str((ctx.get("call_site") or {}).get("line", "")),
        ctx.get("bullet", ""),
        ctx.get("prompt_version", PROMPT_VERSION),
    ])
    return hashlib.sha256(sig.encode()).hexdigest()[:24]


def cache_get(key):
    try:
        return json.load(open(os.path.join(CACHE_DIR, key + ".json")))
    except Exception:
        return None


def cache_put(key, contract):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, key + ".json"), "w") as f:
            json.dump(contract, f)
    except Exception:
        pass
