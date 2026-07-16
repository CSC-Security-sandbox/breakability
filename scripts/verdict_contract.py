# Backward-compatible shim — canonical location is core/verdict_contract.py
from core.verdict_contract import *  # noqa: F401,F403

if __name__ == "__main__":
    import importlib, sys
    mod = importlib.import_module("core.verdict_contract")
    code = compile(open(mod.__file__).read(), mod.__file__, "exec")
    exec(code, {"__name__": "__main__", "__file__": mod.__file__})
