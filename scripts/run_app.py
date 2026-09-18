"""Start the Autonomous Web Testing Platform without installing the package first.

`python -m web_testing_agent.app` is the documented entry point and is what you should use
once `pip install -e .` has been run. This script exists for a bare clone, where `src/` is
not yet on the import path — it adds it, exactly as every other script in this directory
does, and then defers to the same `__main__`. There is no second implementation here.

    python scripts/run_app.py
    python scripts/run_app.py --port 8080
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
