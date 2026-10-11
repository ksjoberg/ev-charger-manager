"""Test setup: load the integration's pure-Python modules without Home Assistant.

The package ``__init__`` imports Home Assistant, so the planner and strategy
modules are loaded through a stand-in package that only points at the source
directory.
"""

import sys
import types
from pathlib import Path

PKG_DIR = Path(__file__).parents[1] / "custom_components" / "ev_charger_manager"

_pkg = types.ModuleType("ev_charger_manager_pure")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules.setdefault("ev_charger_manager_pure", _pkg)
