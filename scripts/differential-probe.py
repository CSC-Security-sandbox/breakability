#!/usr/bin/env python3
"""Differential probe (agent-driven behavioral verification).

Shim -- the implementation has been split into the probe/ package.
All names are re-exported here for backward compatibility.
"""
import os
import sys

# When loaded via exec(open(...).read()), __file__ is not defined --
# fall back to the current working directory.
try:
    _here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _here = os.getcwd()
sys.path.insert(0, _here)
from probe.differential_probe import *  # noqa: E402,F401,F403

if __name__ == "__main__":
    sys.exit(main())
