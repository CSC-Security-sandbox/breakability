"""Backward-compat shim — delegates to ecosystems/ecosystem_adapters.py"""
import importlib as _il, os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
_sys.modules[__name__] = _il.import_module("ecosystems.ecosystem_adapters")
