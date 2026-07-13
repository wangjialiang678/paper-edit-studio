#!/usr/bin/env python3

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _ensure_supported_python() -> None:
    if sys.version_info >= (3, 11):
        return
    for name in ("python3.14", "python3.13", "python3.12", "python3.11"):
        candidate = shutil.which(name)
        if candidate:
            os.execv(candidate, [candidate, *sys.argv])
    raise SystemExit("Python 3.11+ is required. Install python3.11 or run with a newer interpreter.")


def main() -> int:
    _ensure_supported_python()
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=root, env=env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
