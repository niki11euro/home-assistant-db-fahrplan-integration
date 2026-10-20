"""Load pure model/API modules without importing a full Home Assistant server."""

import sys
from pathlib import Path
from types import ModuleType

PACKAGE = "custom_components.db_fahrplan"
root = Path(__file__).resolve().parents[1]
for name, location in (
    ("custom_components", root / "custom_components"),
    (PACKAGE, root / "custom_components" / "db_fahrplan"),
):
    if name not in sys.modules:
        module = ModuleType(name)
        module.__path__ = [str(location)]
        sys.modules[name] = module
