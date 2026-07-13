"""Aggregator -- re-exports all public names from differential probe sub-modules.

This module exists so that ``from probe.differential_probe import *`` brings
every name that was previously available in the monolithic differential-probe.py
into the caller's namespace, preserving backward compatibility.
"""
from .config import *      # noqa: F401,F403
from .utils import *       # noqa: F401,F403
from .evidence import *    # noqa: F401,F403
from .grading import *     # noqa: F401,F403
from .sandbox import *     # noqa: F401,F403
from .npm_probe import *   # noqa: F401,F403
from .gomod_probe import *  # noqa: F401,F403
from .cache import *       # noqa: F401,F403
from .orchestrator import *  # noqa: F401,F403

# Build __all__ from sub-module __all__ lists so private names that tests
# rely on (e.g. _observed_output_is_real, _PRIVATE_SCOPES_CACHE) pass
# through ``from probe.differential_probe import *``.
from . import (
    config as _cfg, utils as _utl, evidence as _evi, grading as _grd,
    sandbox as _sbx, npm_probe as _npm, gomod_probe as _gom,
    cache as _cch, orchestrator as _orc,
)

__all__ = []
for _m in (_cfg, _utl, _evi, _grd, _sbx, _npm, _gom, _cch, _orc):
    __all__.extend(getattr(_m, '__all__', []))
