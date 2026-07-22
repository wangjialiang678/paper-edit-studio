#!/usr/bin/env python3
"""Paper Edit 无头 CLI 启动入口。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cutpoint_lab.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
