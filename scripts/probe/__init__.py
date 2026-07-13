"""Differential probe package -- modularized from differential-probe.py.

Ensures the scripts/ directory (parent of probe/) is on sys.path so that
sibling modules (break_class_router, cross_pr_reconciler) are importable
from sub-modules.
"""
import os
import sys

_scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from .differential_probe import *  # noqa: E402,F401,F403
