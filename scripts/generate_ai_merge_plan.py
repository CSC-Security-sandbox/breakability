"""Backward-compat shim — delegates to ai/generate_ai_merge_plan.py"""
import importlib as _il, os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
_mod = _il.import_module("ai.generate_ai_merge_plan")
_sys.modules[__name__] = _mod
if __name__ == "__main__":
    _sys.exit(_mod.main())
