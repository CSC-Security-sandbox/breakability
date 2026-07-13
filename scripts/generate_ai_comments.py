"""Backward-compat shim — delegates to ai/generate_ai_comments.py"""
import importlib as _il, os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
_sys.modules[__name__] = _il.import_module("ai.generate_ai_comments")
