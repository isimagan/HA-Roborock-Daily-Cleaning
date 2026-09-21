"""Test setup that keeps framework-independent tests lightweight."""

import sys
from pathlib import Path
from types import ModuleType

# Import pure submodules without executing the integration package __init__,
# which requires a complete Home Assistant runtime.
package = ModuleType("custom_components.daily_cleaning")
package.__path__ = [
    str(Path(__file__).parents[1] / "custom_components" / "daily_cleaning")
]
sys.modules["custom_components.daily_cleaning"] = package
