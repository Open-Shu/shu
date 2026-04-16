"""File-path-based module loader for plugin-module unit tests.

The sandbox and host modules have to be loadable individually without
pulling in the full ``shu`` package (heavy service deps, side effects
at import time). Tests work around this by loading each module directly
from its file path via ``importlib.util.spec_from_file_location``.

Every test file was carrying its own copy of this helper. This module
is the single source of truth; access it via ``from _module_loader
import load_module`` (see ``conftest.py`` for the sys.path wiring).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_module(module_name: str, file_path: Path) -> ModuleType:
    """Load *file_path* as *module_name*, registering it in ``sys.modules``.

    Idempotent: returns the cached module if *module_name* is already
    imported. Callers rely on that so modules with interdependencies
    (e.g. ``rpc_server`` importing ``rpc``) resolve each other without
    re-executing module bodies.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {module_name!r} from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod
