import sys
from pathlib import Path

# Make the ui/ modules (state, stages, ...) importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = ["nicegui.testing.user_plugin"]
