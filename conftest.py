"""Put ``src/`` on the import path so ``import llm_metrics`` works without an
editable install. Keeps the Phase 0 self-tests runnable with a bare ``pytest``.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))
