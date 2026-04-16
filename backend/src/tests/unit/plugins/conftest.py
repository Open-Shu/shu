"""Pytest bootstrap for plugin-module unit tests.

Two jobs:

1.  Put this directory on ``sys.path`` so sibling helper modules
    (``_module_loader``) can be imported by tests as ``from
    _module_loader import load_module``. Pytest's default rootdir
    fallback only adds the *test file's own* directory; helpers one
    level up are otherwise invisible.

2.  Re-export :func:`load_module` so test files can reach it either
    way without caring about the plumbing.
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from _module_loader import load_module  # noqa: E402,F401  (re-exported)
